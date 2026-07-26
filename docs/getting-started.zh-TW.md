# 快速上手

!!! note "Terminology rule (zh-TW pages)"
    技術名詞首次出現以「中文 (English original)」格式呈現，例：依賴注入
    (dependency injection)。**不自創翻譯**——若無公認譯名直接保留英文
    （如 `embedding`、`tokenizer`）。代碼、API 名、CLI flag、套件名、檔名一律不翻。

第一次使用，從頭走到尾：安裝 pueue、安裝擴充功能 (extension)、送出一個任務、把佇列
(queue) 放上 menu bar，然後讀一次失敗紀錄。

本擴充功能是去呼叫 `pueue` 這個命令列工具 (command line tool)。它自己不排任何隊，
所以第 1 步不是可選的。

## 1. 安裝 pueue 並啟動 daemon

```sh
brew install pueue
# or
cargo install --locked pueue
```

接著啟動 daemon：

```sh
brew services start pueue
```

或者**從終端機**執行：

```sh
pueued -d
```

!!! warning "請從終端機或 `brew services` 啟動 daemon，不要從 GUI 應用程式啟動"

    `pueued` 會把自己的環境交給**它日後執行的每一個任務**。若它是被某個 GUI 行程
    (process) 當成子行程 (child process) 啟動的，那份環境沒有 `~/.zshrc`、`PATH`
    也很貧乏，而你送出的每個任務都會繼承它。擴充功能那個一鍵的 *Start Daemon* 動作
    只在 `brew services` 管理 daemon 時才會出現，這樣 daemon 歸 launchd 所有，
    而不是歸 Raycast。

用一個指令同時驗證 client 與 daemon 兩邊：

```sh
pueue status
```

安裝正確的話會印出一張表格（是空的沒關係，出現 `Task list is empty` 也沒關係）。
如果 daemon 沒起來，你拿到的會是錯誤訊息；見下方的[疑難排解](#troubleshooting)。

順便看一下版本：

```sh
pueue --version
```

!!! note "只支援 pueue 4.x"

    Pueue 4.0 以不相容的方式改了狀態格式 (state format)——任務的時間戳記
    (timestamp) 與結果被搬進 status enum 裡面——所以不支援 pueue 3.x。

## 2. 安裝擴充功能

本擴充功能**還沒上 Raycast store**。目前請從原始碼執行：

```sh
git clone https://github.com/daviddwlee84/Pueue-Raycast-Extension.git
cd Pueue-Raycast-Extension
npm install
npm run dev
```

`npm run dev`（`ray develop`）會把擴充功能註冊進 Raycast 的 root search。用 `Ctrl-C`
停掉 dev 行程之後它仍然保持註冊，所以從此就能正常使用——只有要讓程式碼變更生效時，
才需要再跑一次 `npm run dev`。

打開 Raycast 輸入 `Pueue`，應該會出現五個指令：

| 指令 | 功能 |
| --- | --- |
| **Tasks** | 依狀態分組瀏覽佇列，附詳細資訊面板 (detail pane) 與 log 預覽。可以帶一段 pueue 查詢 (query) 當作參數，例如 `status=failed order_by id desc`。 |
| **Add Task** | 送出一個指令，可指定工作目錄 (working directory)、群組 (group)、標籤 (label)、優先權 (priority)、依賴 (dependencies) 與延遲 (delay)。 |
| **Quick Add Task** | 直接從 root search 送出指令，沿用你上次用過的群組與目錄。 |
| **Groups** | 暫停與恢復群組、調整平行度 (parallelism)、新增與移除群組。 |
| **Queue Menu Bar** | 在 menu bar 顯示 running / queued / failed 的數量，並提供各任務的操作。 |

## 3. 送出你的第一個任務

打開 Raycast，輸入 `Quick Add Task`，按 `Tab` 移到參數欄，貼上一個會跑個幾秒的指令，
方便你看它執行：

```sh
sleep 20 && echo done
```

按 `⏎`。會有一則 HUD 確認 `Queued task 0 · sleep 20 && echo done`。不會有視窗——
**Quick Add Task** 是 `no-view` 指令，執行完就結束。

第一次執行時，它會送進 `default` 群組，工作目錄用你的家目錄。之後就沿用 **Add Task**
上一次用過的設定，所以常見情境完全不需要設定。想自己挑目錄、群組、標籤、優先權、
依賴或延遲時，再用 **Add Task**。

現在打開 **Tasks**。新任務會出現在 *Running* 底下，顯示指令、所屬群組，以及已經跑了多久。
選取它，詳細資訊面板就會填入中繼資料 (metadata) 與最後 20 行 log。

!!! note "任務跑在 daemon 的環境裡"

    pueue 是在 **pueued** 的環境（而不是你 shell 的環境）裡，以
    `sh -c '<your command>'` 執行每個指令。如果某個指令依賴 `~/.zshrc` 加進 `PATH`
    的路徑，可能得改用絕對路徑 (absolute path)，或在 `pueue.yml` 的 `daemon.env_vars`
    底下加一筆。指令結尾的 `&` 會把行程 detach，於是任務瞬間就結束了，而真正的工作
    還在沒人看管的情況下繼續跑。

## 4. 打開 menu bar

從 Raycast 的 root search 執行 **Queue Menu Bar** **一次**。

!!! warning "全新安裝在你手動執行一次之前，什麼都不會顯示"

    對於從 store 安裝的擴充功能，Raycast 會停用背景重新整理 (background refresh)，
    直到該指令第一次被開啟為止。在那之前，menu bar 上根本沒有這個項目。執行它一次，
    或在該指令的設定裡啟用背景重新整理，它就會出現，並開始每分鐘自行更新。

    這是最常被回報成「它壞了」的情況，而它並不是擴充功能的 bug。

跑起來之後，menu bar 上會有一個 pueue 圖示，旁邊帶一個數字。預設那個數字是 **running**
的數量。該指令的設定可以改變它算的是什麼：

| Menu Bar Title | 顯示 |
| --- | --- |
| Running count *(預設)* | running |
| Running / queued | 兩者分開顯示 |
| Running + queued + paused | 合成一個總數 |
| Icon only | 完全不顯示數字 |

**數量為零時標題會消失。** Raycast 沒有 badge API——那個數字*就是*標題，而空標題渲染
出來就只剩圖示。所以閒置的佇列是一個光禿禿的圖示，不是 `0`。

打開選單可以看到 Running、Queued、Failed 三個區塊與各任務的操作、一個帶暫停/恢復與
平行度選擇器的 Groups 區塊，以及一列 `Updated HH:MM`。最後那一列之所以存在，是因為
Raycast 是從資料庫還原 menu bar 的畫面，而不是重新執行指令，所以過期的畫面可能在重啟
之後還留著——這個時間戳記讓「資料過期」這件事看得見，而不是繼續誤導人。

!!! tip "menu bar 裡的破壞性操作藏在 `⌥` 後面"

    Raycast 的確認對話框 (confirmation dialog) 顯示在 Raycast 視窗裡，而選單開啟時
    那個視窗是關著的。與其冒著確認被默默吞掉的風險，kill 與 remove 只在你按住 `⌥`
    時才會出現，且不受 *Confirm destructive actions* 偏好設定影響。

如果你的佇列裡躺著好幾千個已完成的任務，把 **Menu Bar Query** 設成 `last 100`，
讓每分鐘一次的讀取維持在便宜的範圍。

## 5. 讀一次失敗

送出一個會失敗的指令：

```sh
ls /definitely-not-a-directory
```

打開 **Tasks**。它會落在 *Failed* 底下，附上退出碼 (exit code)。

**詳細資訊面板**（`⌘⇧D` 切換）顯示指令、工作目錄、群組、標籤、時間戳記、退出碼，
以及最後 20 行 log 的預覽——足以在不離開清單的情況下辨識出大多數的失敗原因。
預覽行數可用 Tasks 指令上的 *Log Preview Lines* 偏好設定調高或調低；每切換一次選取，
就多付一次 `pueue log` 呼叫的成本。

**log 檢視**（`⏎`）顯示完整輸出，直接從磁碟上 pueue 的 `task_logs/` 目錄讀取。
`⌘⇧F` 會複製 `pueue follow <id>`，方便你改在終端機裡看。

**跟看即時輸出**（`⌘L`）適用於執行中的任務：它會串流 `pueue follow`，該指令每 250 ms
輪詢一次磁碟上的 log。任務執行期間標題顯示 *Following*，結束時翻成 *Finished*——
`follow` 退出代表任務結束，不是錯誤。離開這個畫面會終止子行程。

Tasks 其餘的快捷鍵：

| 快捷鍵 | 動作 |
| --- | --- |
| `⏎` | 顯示 log |
| `⌘L` | Follow 輸出（執行中的任務） |
| `⌘⇧R` | Restart 成一個新任務 |
| `⌘⌥R` | 原地 Restart——同一個 id，**會覆寫 log** |
| `⌘⇧P` | Pause · `⌘⇧S` Resume / Start now |
| `⌘⇧T` | Stash · `⌘⇧E` Enqueue |
| `⌘⇧K` | Kill · `⌘⌫` Remove |
| `⌘⇧D` | 切換詳細資訊面板 · `⌘R` Reload |

失敗時的常見循環是：讀 log、修指令、`⌘⇧R` 以新任務重跑。只有在你想保留原本的 id 時
才用 `⌘⌥R`——它會覆寫既有的 log。

只想看失敗的任務，就把查詢當成該指令的參數傳進去：

```text
status=failed order_by id desc
```

## 6. 疑難排解 { #troubleshooting }

### 「終端機裡明明可以，擴充功能卻找不到 pueue」

症狀：出現 **Pueue CLI not found** 或 `spawn pueue ENOENT`，但你在終端機打
`which pueue` 卻立刻有答案。

Raycast 是在 launchd 底下執行擴充功能，而 launchd 從來不會去 source `~/.zshrc`、
`~/.zprofile` 或任何其他 shell rc 檔。Homebrew 的 `bin` 與 `~/.cargo/bin` 正是*由那些
檔案*加進 `PATH` 的，所以擴充功能拿到的 `PATH` 大概就只有 `/usr/bin:/bin`，
光打一個 `pueue` 是找不到的。

擴充功能從不直接呼叫裸的執行檔名稱。它會依序探測：

```text
/opt/homebrew/bin   Apple Silicon Homebrew
/usr/local/bin      Intel Homebrew
~/.cargo/bin
~/.local/bin
```

如果你的裝在別的地方，把它找出來然後明確指定：

```sh
which pueue
```

把結果貼到 Raycast → Extensions → Pueue → **Pueue Binary Path**。

!!! note "dev 終端機會把這一類 bug 藏起來"

    `npm run dev` 的 console 繼承了你完整的互動式 `PATH`，所以跟 PATH 有關的問題
    可能在那裡好好的、到正式環境卻失敗。要驗證 PATH 相關的改動，請從 Raycast
    操作——root search 或 menu bar——不要從 dev 終端機。背景說明：
    [pitfalls/raycast-launchd-path-pueue-not-found.md](https://github.com/daviddwlee84/Pueue-Raycast-Extension/blob/main/pitfalls/raycast-launchd-path-pueue-not-found.md)。

### 「Pueue daemon not running」

pueue 裝好了，但連不上 `pueued`。擴充功能會直接這樣告訴你，並附上一鍵的
**Start Daemon (brew services)** 動作——但只有在 `brew services` 已經在管理 daemon
時才有，因為從 Raycast 啟動 `pueued` 會讓它變成 Raycast 那個 launchd 行程的子行程，
並把那份被剝光的環境交給它日後執行的每一個任務。

否則就自己啟動，然後用 `⌘R` 重新載入：

```sh
brew services start pueue
# or
pueued -d
```

兩種錯誤字串意思不同：

- `Did you start the daemon at least once?` —— `pueued` **從來沒**跑過，所以還沒有
  shared secret 檔案。
- `while connecting to daemon` —— 它跑過，之後停了。

## 下一步

- [遠端 daemon](remote.md) —— 監看與操控另一台機器上的 `pueued`。設定就是在
  **Remote Connections** 偏好設定裡填一行：一台你本來就連得上的 SSH 主機 (SSH host)，
  例如 `local_ubuntu`。不用隧道 (tunnel)、不用 shared secret、不用設定檔。經過多工
  (multiplexing) 的 SSH 實測**每次呼叫 10–30 ms**，對照本機 pueue 的 22–44 ms
  （沒有多工則是 200–400 ms，這正是擴充功能一律帶上 `ControlMaster` 的原因）。
  送出任務之所以行得通，是因為 client 跑在遠端機器上，工作目錄在那裡才真的解析得出來。
- [pueue 的 JSON 契約](pueue-json-contract.md) —— 擴充功能從 `pueue status --json`
  與 `pueue log --json` 裡解析出哪些東西。
- [Raycast 的介面形式](raycast-surfaces.md) —— 為什麼 menu bar 是唯一一直看得見的
  介面。Raycast **沒有給擴充功能用的 widget API**；一個 `mode: "menu-bar"` 指令
  就是全部了。
- 任務結束時的桌面通知是 pueue 的功能，不是擴充功能的——本擴充功能從不寫入你的
  `pueue.yml`。`daemon.callback` 的片段在
  [README](https://github.com/daviddwlee84/Pueue-Raycast-Extension/blob/main/README.md#notifications-when-a-task-finishes)
  裡。
- [TODO.md](https://github.com/daviddwlee84/Pueue-Raycast-Extension/blob/main/TODO.md)
  —— 接下來規劃了什麼。
