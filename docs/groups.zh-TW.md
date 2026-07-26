# Group 與批次進度

!!! note "Terminology rule (zh-TW pages)"
    技術名詞首次出現以「中文 (English original)」格式呈現，例：依賴注入
    (dependency injection)。**不自創翻譯**——若無公認譯名直接保留英文
    （如 `embedding`、`tokenizer`）。代碼、API 名、CLI flag、套件名、檔名一律不翻。

group 是 pueue 的並行 (concurrency) 單位：每個 group 有自己的並行上限與
running/paused 狀態，而每個 task 只屬於一個 group。所以它天然就是放一個**批次
(batch)** 的地方——把二十個 job 丟進 `wf`，然後看它們一個個落地。

這頁講的是本擴充功能為此顯示的那些數字：它們是什麼意思，以及每一個數字在哪裡
刻意停下來、不去猜。

## 一列 group 顯示什麼

```text
◕  wf          6/20 done · 2 running · 3 failed        ~4m left    Running
```

| 部位 | 意義 |
| --- | --- |
| 圓環 | `done / total`，以填滿的圓表示 |
| 圓環顏色 | 有任何失敗為紅、group 被暫停為橙、執行中為藍、全部完成且無失敗為綠 |
| `6/20 done` | 已結束的 task 除以這個 group 裡的所有 task |
| `2 running` 等 | 只列出真的有 task 的狀態，數量為 0 的一律不寫 |
| `~4m left` | 一個估計值——見[下方](#eta) |
| 標籤 (tag) | group 自己的狀態，跟它底下 task 的狀態不是同一回事 |

按 <kbd>⌘</kbd><kbd>⇧</kbd><kbd>D</kbd> 打開 detail pane，多出平均耗時、已經跑了
多久、以及失敗的 task id。section 標題則直接顯示整個 queue 的彙總。

menu bar 顯示同一份東西的精簡版——`wf · 6/20 · 3 failed`，進度條與 ETA 放在
submenu 裡。

## 為什麼 `total` 算進 group 裡的所有 task

pueue 沒有「批次」這個概念。沒有 submission id、沒有 run number，沒有任何欄位標記
「這二十個是一起的」。真正存在的邊界只有 `pueue clean`。

所以 `total` 就是這個 group 目前的所有 task；只要你在兩批之間清一次，批次的數字就是
對的：

```sh
pueue clean --group wf     # 或用「Clean Finished Tasks in Group」這個 action
pueue add -g wf -- ./job-1
pueue add -g wf -- ./job-2
# …
```

另一種做法——例如推論「從這個 group 上次閒置到現在的都算一批」——會是把猜測當成事實
呈現，而且恰恰會在「這個數字最重要」的長時間 group 上出錯。

## finished 是「結束」，不是「成功」

`6/20 done` 計入所有 pueue 回報為 `Done` 的 task，不管結果如何。一個 exit 127 的
task 是結束了；它不會自己再跑一次。失敗數在同一行另外列出，也就是把圓環染紅的東西。

這跟 `pqsum` 的定義一致，而且只有這樣讀，圓環才會在 queue 真的停下來時走到 100 %。

## ETA

`~4m left` 是 `pending × 平均耗時 ÷ 並行數`。背後有三個刻意的選擇：

- **pending 指 running、queued、paused。** stashed 被排除在外。stashed 的 task 在等
  一個人，不是在等 queue，把它算進來會讓估計值毫無上限地膨脹。它仍然留在 `total`
  裡，因為那終究是你交代的工作。
- **至少要有兩個已完成的 task。** 只有一個樣本時，「平均」就是那一個 task 本身——那是
  一個穿著數字外衣的猜測。不到兩個，ETA 顯示為 `—`。
- **unlimited 當作一個 slot 算。** `parallel_tasks: 0` 是 pueue 的 unlimited；直接
  拿來當除數會除以零，取 1 是保守的答案。

它是平均值，不是模型 (model)。二十個一模一樣的 job 會給出好的估計；一個 3 秒的 linter
配一個 40 分鐘的 build 則不會——`~` 就是在說這件事。

## Elapsed

從最早的 start 到最晚的 end——若還有東西在跑或被暫停，則算到現在為止。沒有東西在跑時
它會凍結，因為一個「task 卡在被暫停的 slot 後面」的 group，並不是「已經跑了三小時」。

## 批次 action

有四個 action 作用於整個 group，而不是單一 task。

| Action | 對應的 pueue 指令 | 說明 |
| --- | --- | --- |
| Restart *n* Failed (New Tasks) | `restart --not-in-place --failed-in-group NAME` | 產生新的 id，原本的 task 保留自己的 log。 |
| Restart *n* Failed in Place | `restart --in-place --failed-in-group NAME` | 沿用原本的 id，並**覆寫 log**。還需要那些 log 的話請先讀。 |
| Clean Finished Tasks in Group | `clean --group NAME` | 移除已結束的 task 及其 log，不動 running 或 queued 的。 |
| Clean Only the Successes | `clean --group NAME --successful-only` | 失敗的留著，讓你還能讀 log。 |

restart 這兩個 action 在 group 沒有失敗時是**隱藏**，不是變灰。pueue 對一個沒有失敗
的 group——甚至對一個根本不存在的 group——執行 `--failed-in-group` 都會安靜地 exit 0。
一個永遠看得到卻什麼都不做的 action，比一個不存在的 action 更糟。

## 兩個「名字沒說完」的操作

兩者都是從 `pueue --help` 讀出來的，而且兩個確認對話框都會明講：

- **Kill Running Tasks in Group** 同時會**暫停**這個 group。在你恢復它之前不會再有
  新的 task 開始。
- **Remove Group** 是把它的 task **搬到** `default`，而不是刪掉。

## Reset

reset 會殺掉這個 group 的每一個 task、刪掉它們、也刪掉它們的 log。這是本擴充功能中
唯一每一次都會問、而且不提供「不要再顯示」的 action。

在 menu bar 中它躲在 <kbd>⌥</kbd> 後面，**且不受 Confirmations 這個 preference 影響**
——這是唯一的例外，因為 menu bar 沒有對話框可用（menu 打開時 Raycast 的 alert 無法
顯示），而這又是唯一一個點錯就救不回來的 action。想要真正的確認對話框的話，
`Open Groups…` 就在它下面一格。

## 並行數 (parallelism)

submenu 提供 1–32 與 Unlimited，另外有一個 `Custom…` 欄位可以填任何數字——pueue 接受
任何非負整數，一台 64 核的機器完全有資格要求 48。

這些預設值是刻意寫死的清單。拿本機的核心數來當預設，在你把擴充功能指向遠端 daemon 的
那一刻就是錯的，而一個看起來合理的錯誤數字，比一個什麼都不主張的清單更糟。
