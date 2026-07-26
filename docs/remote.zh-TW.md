# 操控遠端的 pueued

!!! note "Terminology rule (zh-TW pages)"
    技術名詞首次出現以「中文 (English original)」格式呈現，例：依賴注入
    (dependency injection)。**不自創翻譯**——若無公認譯名直接保留英文
    （如 `embedding`、`tokenizer`）。代碼、API 名、CLI flag、套件名、檔名一律不翻。

pueue 無法*跨主機*排程，但本機的 client 可以完整操控另一台機器上的 `pueued`——
`status`、`log`、`follow`、`kill`、`add` 都能用。

這件事不需要本擴充功能寫任何協定 (protocol) 程式碼：一個連線就只是一份 client 設定，
其餘交給 pueue 自己的 client 處理。真正要做的，是把四個原本默默假設「在本機」的地方
改掉。

> 以下設定流程，來自一次實測：macOS client（pueue 4.0.4）透過 SSH socket 轉發
> 操控 Linux 上的 `pueued`（4.0.2）。

## 最短版本

Preferences → **Remote Connections**，填入：

```text
local_ubuntu
```

一台你本來就連得上的 SSH 主機 (SSH host)。沒別的了——不用隧道 (tunnel)、不用密鑰、
不用設定檔。每個指令都以 `ssh local_ubuntu 'pueue …'` 執行。

多個連線寫在同一行，用分號 `;` 隔開；一個連線內部的欄位仍然用 `|` 隔開。分號不是隨便選
的——Raycast 的 preference 只有單行的 `textfield` 可用，設定欄位裡根本打不出換行：

```text
lab | local_ubuntu | ~/.cargo/bin/pueue ; gpu | gpu.example.com
```

換行仍然會被接受，但那只對以其他方式寫入的值有意義；設定介面產生不出換行。

**為什麼這是預設做法，而不是退路。** 對區域網路內的主機實測：

```text
plain ssh, one connection per call    200–400 ms
with ControlMaster multiplexing        10–30 ms
local pueue status --json              22–44 ms
```

經過多工 (multiplexing) 的遠端讀取，成本跟本機讀取差不多，所以轉發 socket 能再省下的
空間已經不多。擴充功能一律帶上 `ControlMaster=auto -o ControlPersist=120`；ssh 會自己
讓共用連線過期，因此不像隧道那樣有東西要啟動、盯著、或善後。

需求：

- 遠端主機上有可執行的 `pueue`。**`ssh host 'cmd'` 跑的是非互動式 shell
  (non-interactive shell)**，不會讀取任何 rc 檔——所以即使你登入後 `which pueue` 找得到，
  裝在 `~/.cargo/bin` 的 `cargo install` 版本對它仍是隱形的。這種情況夠常見，因此連線
  設定可以直接指名路徑：

  ```text
  lab | local_ubuntu | ~/.cargo/bin/pueue
  ```

  接在*主機*後面的第三個欄位是遠端執行檔；接在*設定檔路徑*後面的則是 SSH 主機。兩者不會
  混淆，因為主機名稱裡永遠不會有斜線。`~` 是在對面那端展開，不是在這裡。

  少了它你會拿到 `zsh:1: command not found: pueue`，擴充功能會偵測並說明這個錯誤，而不是
  把原始訊息直接丟出來。
- 金鑰 (key) 或 agent 認證。`BatchMode=yes` 已設定，所以密碼提示會直接快速失敗，而不是
  卡在那裡等你在一個根本不存在的終端機裡輸入。

在這個模式下，送出任務 (submission) 自然就是對的：client 跑在遠端機器上，工作目錄
(working directory) 是在它真正存在的地方解析。這正是本文其餘篇幅要繞開的問題。

## 進階：透過轉發的 socket 讀取

### 1. 轉發 daemon 的 socket

伺服器端**完全不用重新設定**——維持它預設的 unix socket 即可。

```sh
ssh myhost 'ls /run/user/$(id -u)/pueue_*.socket'
# /run/user/1000/pueue_myuser.socket

ssh -f -N -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
    -L ~/.config/pueue/remote/remote.sock:/run/user/1000/pueue_myuser.socket \
    myhost
```

重新轉發之前，先刪掉本機殘留的 socket 檔。用 `autossh` 或 launchd agent 可以讓它保持連線。

### 2. 複製 shared secret

client 使用與 daemon *相同*的 shared secret 檔案來認證。它是一份憑證：任何人只要拿到它、
又能連到那個 socket，就能以 daemon 所屬使用者的身分執行指令。

```sh
mkdir -p ~/.config/pueue/remote && chmod 700 ~/.config/pueue/remote
ssh myhost 'cat ~/.local/share/pueue/shared_secret' > ~/.config/pueue/remote/shared_secret
chmod 600 ~/.config/pueue/remote/shared_secret
```

### 3. 寫一份只給 client 用的設定

```yaml
# ~/.config/pueue/remote/client.yml
client:
  read_local_logs: false          # REQUIRED — see below
shared:
  use_unix_socket: true
  unix_socket_path: /Users/you/.config/pueue/remote/remote.sock
  shared_secret_path: /Users/you/.config/pueue/remote/shared_secret
```

因為隧道的終點就是 daemon 自己的 unix socket，**過程中完全沒有 TLS 憑證
(TLS certificate) 參與**。直接走 TCP+TLS 也可以，但那需要改伺服器端設定並重啟 `pueued`，
會中斷正在跑的佇列。

### 4. 告訴擴充功能

```text
gpu-box | ~/.config/pueue/remote/client.yml | myhost
```

第二個欄位如果含有 `/`（或以 `.yml` 結尾），會被視為設定檔路徑，也就選擇了 socket 模式。
第三個欄位仍然是 SSH 主機，而且仍然必要——送出任務時，socket 解決不了工作目錄的問題。

擴充功能解析不了的那一段連線，會在任務列表中顯示為 **Unreadable connection**，而不是默默忽略。

接著 Tasks 與 Groups 的 Action Panel 中會出現 **Connection** 子選單 (submenu)（⌘⇧N），
Add Task 表單上則會出現 **Daemon** 下拉選單 (dropdown)。若沒有設定任何遠端連線，這些介面
一律不會出現。

## `read_local_logs: false` 是必填

一定要設。有兩件事靠它：

1. **pueue 本身**否則會試圖在你的本機檔案系統上讀取遠端 daemon 的 log 目錄。
2. **本擴充功能**會讀同一個設定，來判斷自己那條讀取磁碟 log 的快速路徑 (fast path) 是否
   安全，這樣它就不可能跟 pueue 對「log 在哪裡」有不同的認知。

如果留在預設值 `true`，本機的 log 目錄通常*是存在的*——於是讀取會回傳**同一個 id 底下另一個
任務的輸出**，而不是報錯。這是靜默的錯誤，所以它是硬性要求，而非建議。

## 送出任務：用 SSH 主機

**這就是陷阱。** pueue 會在送出當下記錄任務的工作目錄，並在跑 **client** 的那台機器上做
canonicalise。從這裡對遠端 daemon 送出任務，會用三種方式失敗：

| 你做的事 | 發生什麼 |
|---|---|
| 從本機目錄送出 | 送出去的是本機路徑。它在 daemon 上並不存在 → `FailedToSpawn`，永遠不會執行。 |
| 輸入只有遠端才有的路徑 | 本機 client **直接拒絕**：`Failed to canonicalize given working directory path`。 |
| 在 macOS 上輸入 `/tmp` | 會被靜默改寫成 `/private/tmp` → 在 Linux 上失敗。 |

只有那些*在兩台機器上都存在、而且 canonicalise 結果完全相同*的路徑才行得通。你實際的專案目錄
基本上都不符合。

所以當一個連線帶有 SSH 主機時，**Add Task 會以 `ssh <host> 'pueue add …'` 送出**——client
跑在對面那端，路徑是真的。讀取與控制類指令則繼續走轉發的 socket，比較快，也不需要第二次認證。

讓這件事安全的引號處理 (quoting) 有寫成 assertion，並且實測驗證過：把
`echo "double" && echo 'single' && echo HOME=$HOME` 先丟進 quoter，再丟進真正的 shell 跑。

## 其他值得知道的事

- **任務 id 對 daemon 而言是全域 (global) 的。** 一個遠端連線看得到、也刪得掉那台機器上
  *所有人*的任務。使用 Clean 與 Reset 時要小心；優先用標籤 (labels)。
- **SSH 模式沒有版本落差 (version skew) 問題。** client 跑在遠端機器上，所以永遠跟它的
  daemon 同版本。這一點只在 socket 模式下才需要擔心。
- **版本不一致會警告，但仍可運作。** 4.0.4 的 client 對上 4.0.2 的 daemon，會在每個指令
  印出 `Different protocol version detected`，行為則一切正常。擴充功能會把那一行從 stderr
  濾掉，免得它變成 toast 標題、把底下真正的錯誤蓋住。
- **`FailedToSpawn` 屬於 `Done`，不是 `Failed`，** 而且結果是 dict 形狀
  （`{"FailedToSpawn": "<os error>"}`）。擴充功能會把任何不是 `Success` 的終止結果
  (terminal result) 都當成失敗，所以一個從未啟動的工作絕不會被回報為成功。
- **也可以乾脆直接 SSH 過去**，處理一次性的送出。`ssh myhost 'pueue add …'` 完全不用設定。
  當你想從這裡*盯著*一個遠端佇列時，轉發連線才值得。

## 如果不成功

| 症狀 | 原因 |
|---|---|
| `status` 卡住不動 | 隧道斷了。重新轉發。 |
| shared secret 不符 | 重新複製 `shared_secret`。 |
| 任務都變成 `FailedToSpawn` | 工作目錄問題。在連線設定裡加上 SSH 主機。 |
| log 顯示的是別的輸出 | `read_local_logs` 不是 `false`。 |
| `command not found: pueue` | ssh 用的是非互動式 shell。把遠端路徑加成第三個欄位。 |
