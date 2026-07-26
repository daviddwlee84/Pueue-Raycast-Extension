# pueue 的 JSON 契約 (JSON contract)

!!! note "Terminology rule (zh-TW pages)"
    技術名詞首次出現以「中文 (English original)」格式呈現，例：依賴注入
    (dependency injection)。**不自創翻譯**——若無公認譯名直接保留英文
    （如 `embedding`、`tokenizer`）。代碼、API 名、CLI flag、套件名、檔名一律不翻。

`pueue` v4.0.4 實際會吐出什麼，以及每一個「合理推測卻推測錯」的地方。這裡的
內容全部是從真實的 binary 抓下來的，不是從文件抄的——`src/lib/dev-check.ts`
裡的斷言 (assertions) 就是這份文件的可執行版本。

## 只有三個指令會輸出 JSON

`status`、`log`、`group`。其餘十五個變更操作 (mutation) 全部只有結束碼
(exit code) 加一段散文。這就是 `PueueTransport.mutate` 回傳 `void` 的原因，
也是 `add` 必須靠 `--print-task-id` 才擠得出有用資訊的原因。

## 全域 flag 要放在 subcommand 前面

```console
$ pueue status --color never --json
error: unexpected argument '--color' found
$ echo $?
2
```

```console
$ pueue --color never status --json
{"tasks":{},"groups":{"default":{"status":"Running","parallel_tasks":1}}}
```

**結束碼有 0、1、2 三種**——不是只有 0/1。Clap 的參數錯誤是 2，argv 組錯了就
會以這種形式浮上來。

## `status --json`

```jsonc
{
  "tasks":  { "<id>": { /* Task */ } },   // keys are strings; id also appears inside
  "groups": { "<name>": { "status": "Running"|"Paused"|"Reset", "parallel_tasks": 0 } }
}
```

`parallel_tasks: 0` 代表不限制。

### `group --json` 是不一樣的形狀

它只回傳**內層的 map**——`{"default": {...}}`，而不是 `{"groups": {...}}`。
在這個 codebase 裡，讓兩者共用同一個解析器 (parser) 是最容易犯的 bug，所以
`readState` 明確地防範自己被餵到錯的那一種。

### `--group` 過濾 tasks，但不過濾 groups

`status --json --group X` 只過濾 `tasks` map，`groups` 仍然是完整的。傳一個
不存在的 group 名稱會靜默成功。

## `status` 是兩層的 externally-tagged enum

任何地方都不會出現扁平的 `"status": "Running"`。

```jsonc
{"Stashed": {"enqueue_at": null}}
{"Stashed": {"enqueue_at": "2026-07-27T03:00:00.000000+08:00"}}
{"Queued":  {"enqueued_at": "..."}}
{"Running": {"enqueued_at": "...", "start": "..."}}
{"Paused":  {"enqueued_at": "...", "start": "..."}}
{"Locked":  {"previous_status": { /* recursive TaskStatus */ }}}
{"Done":    {"enqueued_at": "...", "start": "...", "end": "...", "result": <TaskResult>}}
```

- **`Locked` 是遞迴的 (recursive)。** 它包住的是編輯結束後任務會回到的那個
  狀態。過濾時請用會把它拆開的 `underlyingKind()`。
- **`enqueue_at` 與 `enqueued_at` 不同。** `Stashed` 用的是前者，可為 null，
  而且指向*未來*——任務*將會*被排入佇列的時間。其他所有變體用的是後者，指向
  過去。
- **`new Date(null)` 會得到 1970-01-01，不是 `Invalid Date`。** 沒有防護就去
  parse 一個單純 stashed 的任務，畫面上會出現 1970。這就是 `parseTs` 存在的
  理由。

### `TaskResult` 混用純字串與物件

| JSON | Meaning |
| --- | --- |
| `"Success"` | 以 0 結束 |
| `{"Failed": 127}` | 非零結束；數字就是結束碼 |
| `{"FailedToSpawn": "..."}` | 根本 spawn 不起來——cwd 有問題、binary 讀不到 |
| `"Killed"` | 被你或被 daemon 關閉時殺掉 |
| `"Errored"` | 內部 IO 錯誤 |
| `"DependencyFailed"` | 某個 `--after` 的上游失敗了；自己從未執行 |

**失敗判定採用允許清單 (allowlist)**：只要是終態且不是 `"Success"` 就算失敗。
用拒絕清單 (denylist) 的話，pueue 下次新增的變體一定會漏掉。

注意，找不到指令**不會**產生 `FailedToSpawn`——pueue 是透過 `sh -c` 執行的，
所以是 shell 回傳 127，你拿到的是 `{"Failed": 127}`。

### 時間戳記 (timestamps)

chrono 的 `DateTime<Local>` → RFC 3339，帶**微秒**與**數字時區偏移 (numeric
offset)**：`2026-04-27T11:01:06.893055+08:00`。永遠不會是 `Z`，也不會是 epoch。
V8 讀得懂多出來的小數位，並截斷到毫秒。

## `envs` 是一份完整的環境變數快照

`status --json` 裡的每個任務都有它，而 `log --json` 裡會被清成 `{}`。在這台機器
上實測：**六個微不足道的任務，帶著它是 53,595 bytes，不帶是 2,509——21 倍，
每個任務 120 個變數。**

它可能含有機密，而 `useCachedPromise` 會把資料寫進 Raycast 以磁碟為底的快取
(cache)。transport 層在 parse 的邊界就把它剝掉；真正需要它的那一處，由
`taskEnvs(id)` 在當下重新讀取。

## `log --json` 把自己的錯誤藏在 output 裡

```console
$ pueue log 1 --json          # task 1 has never run
{"1":{"task":{...},"output":"(Pueue error) Failed to get log file handle: I/O error at path
\"…/task_logs/1.log\" while getting log file handle:\nNo such file or directory (os error 2)"}}
$ echo $?
0
```

結束碼 0，而 pueue 自己的錯誤文字就坐在本該放任務輸出的 `output` 欄位裡。沒有
任何訊號可以拿來分支，所以 `cleanLogOutput()` 靠偵測 `(Pueue error)` 前綴處理，
而 `hasEverRun()` 則是一開始就避免去問。

`pueue log <unknown-id> --json` 會得到 `{}` 與結束碼 0——是空的，不是錯誤。

## 錯誤是 stderr 上的散文，還帶著關不掉的 ANSI

`color_eyre` **即使 stderr 是一條管線 (pipe)** 也照樣往 stderr 寫 SGR escape。
`--color never` 和 `NO_COLOR=1` 都壓不掉。`src/lib/fixtures/stderr.json` 裡收了
七種抓到的失敗形狀；其中真正重要的兩種：

```text
# pueued has NEVER run — no shared secret file yet
I/O error at path "…/shared_secret" while opening secret file.
Did you start the daemon at least once?

# pueued ran and has since stopped (ENOENT, or ECONNREFUSED if the socket remains)
I/O error at path "…/pueue_<user>.socket" while connecting to daemon. Did you start it?
```

兩者都被歸類為 `daemon-not-running`，因為補救方式完全相同；同時原始細節會保留
下來，讓 UI 仍然能告訴使用者實際發生的是哪一種。

## 沒有 push，而且 daemon 的套用落後於它自己的 ack

`pueue wait` 內部每 2 秒輪詢 (polling) 一次，並印出散文。`pueue follow` 會串流
log 文字（已驗證：行會以任務自己的節奏出現），但從不串流狀態。狀態只能靠輪詢
——`status --json` 需要 22–44 ms，中位數 28 ms。

而且 daemon **會在它的更新迴圈真正套用之前就先 ack 這個請求**。殺掉一個執行中
的任務，然後持續輪詢直到 `status --json` 回報 `Done`，五次試驗：

```text
min 278 ms   median 284 ms   max 297 ms
```

ack 本身約 22 ms 就回來了。這就是為什麼每個變更操作都會抑制
`shouldRevalidateAfter`，改成延遲之後再對帳——見 `src/lib/actions.tsx`。

## 動詞沒告訴你的那些行為

| Command | Also does |
| --- | --- |
| `kill --group X` / `kill --all` | 會把該 group **暫停** |
| `group remove X` | 把 X 的任務**搬到** `default`，而不是刪掉它們 |
| `remove <id>` | 拒絕執行中或已暫停的任務；請先 kill |
| `restart --in-place` | 沿用同一個 id，並**覆寫既有的 log** |
| `restart --not-in-place` | 產生新的任務 id（已驗證：id `[1,2]` → `[1,2,3]`） |
| `add --escape` | 會跳脫元字元 (metacharacters)，**連空白也跳脫**——由於指令是以單一 argv 元素傳入，這會把整條指令塌縮成一個 token，因此 UI 不提供這個選項 |
| `parallel` with no argument | 在 4.0.4 是壞的：記錄 "Received unhandled response message"、以 0 結束、什麼都不印。請改從 `group --json` 讀取平行度。 |

## 查詢 DSL

由 pueue **client** 端套用，所以它縮小的是我們要 parse 的量，而不是 daemon 送出
的量。可以與 `--json` 併用。

```text
[columns=id,status,…] [filter]* [order_by <column> asc|desc] [first|last N]

filter columns   status | command | label | start | end | enqueue_at
operators        =  !=  <  >  %=       (%= means "contains")
status values    queued | stashed | paused | running | success | failed
```

pueue 自家文法文件裡的方括號是後設標記 (meta-notation)：`columns=[id]` 會解析
失敗，`columns=id,status` 才可以。

## 指令的跳脫 (escaping)

`pueue add` 接受可變參數 (variadic) 的 `<COMMAND>...`，用空白把它們接起來，再
交給 `sh -c`。本擴充功能把整條指令當作 **`--` 之後的單一 argv 元素**傳入，而且
從不加引號——在這裡加引號會變成雙重跳脫。已驗證：

```console
$ # via argvFor: ["add","--print-task-id","--","echo \"double\" && echo 'single' && echo HOME=$HOME"]
double
single
HOME=/Users/david
```
