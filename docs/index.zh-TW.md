# Pueue for Raycast

!!! note "Terminology rule (zh-TW pages)"
    技術名詞首次出現以「中文 (English original)」格式呈現，例：依賴注入
    (dependency injection)。**不自創翻譯**——若無公認譯名直接保留英文
    （如 `embedding`、`tokenizer`）。代碼、API 名、CLI flag、套件名、檔名一律不翻。

這是一個給 [Pueue](https://github.com/Nukesor/pueue) 任務佇列 (task queue)
daemon 用的 Raycast 擴充功能：瀏覽任務並對它們下指令、即時追蹤輸出、控制群組
(group) 與並行度 (parallelism)，並從選單列 (menu bar) 盯著佇列。它是去呼叫
`pueue` 這個命令列工具，本身不排任何隊；所以在它能做任何事之前，`pueue` 必須先裝好，
`pueued` 也必須正在執行。

## 指令 (Commands)

| Command | What it does |
| --- | --- |
| **Tasks** | 依狀態分組瀏覽佇列，附詳細資訊面板與 log 預覽。可以 restart、kill、pause、stash、enqueue、remove、clean。可帶一個選用的 pueue query 作為參數，例如 `status=failed order_by id desc`。 |
| **Add Task** | 送出一個指令，並指定工作目錄、group、label、優先權 (priority)、依賴 (dependencies) 與延遲 (delay)。 |
| **Quick Add Task** | 直接從 root search 送出指令，沿用你上次使用的 group 與目錄。 |
| **Groups** | 暫停與恢復 group、調整並行度、新增與刪除 group。 |
| **Queue Menu Bar** | 在選單列顯示 running / queued / failed 的數量，並提供針對個別任務的操作。每分鐘更新一次。 |

Raycast 沒有 widget API。`mode: "menu-bar"` 指令是擴充功能唯一能取得的常駐可見介面，
**Queue Menu Bar** 就是這樣一個指令——見 [Raycast 的擴充介面](raycast-surfaces.md)。

## 需求 (Requirements)

安裝 CLI：

```sh
brew install pueue
# or
cargo install --locked pueue
```

啟動 daemon：

```sh
brew services start pueue
```

或者，從終端機：

```sh
pueued -d
```

!!! warning "請從終端機或 `brew services` 啟動 daemon，不要從 GUI app 啟動"

    以 GUI 行程 (process) 的子行程身分啟動的 `pueued`，會繼承那個行程的環境——沒有
    `~/.zshrc`、只有一個很陽春的 `PATH`——然後把這份環境交給**它之後跑的每一個任務**。
    擴充功能裡那個一鍵 *Start Daemon* 的操作，只有在 `pueued` 由 `brew services`
    管理、也就是由 launchd 擁有它的時候才會出現。

本擴充功能是針對 **pueue 4.x** 開發的。Pueue 4.0 以不相容的方式改了 state 格式——
任務的時間戳記與結果被搬進 status enum 裡面——所以不支援 pueue 3.x。

!!! note "擴充功能沒辦法直接用一個裸的 `pueue`"

    Raycast 是在 launchd 底下執行擴充功能的，而 launchd 從來不會去 source 你的 shell
    rc，所以即使 Homebrew 和 `~/.cargo/bin` 在你的終端機裡是通的，它們並不在 Raycast
    的 `PATH` 上。擴充功能一律以絕對路徑呼叫，並會去探測 `/opt/homebrew/bin`、
    `/usr/local/bin`、`~/.cargo/bin`、`~/.local/bin`。如果你的裝在別的地方，請設定
    **Pueue Binary Path**。

## 背景更新 (background refresh) 預設是關的

!!! warning "剛裝好的 menu bar 指令，在你手動跑過一次之前不會顯示任何東西"

    對於從 store 安裝的擴充功能，Raycast 會停用背景更新，直到該指令第一次被開啟為止。
    從 Raycast 的 root search 執行一次 **Queue Menu Bar**，或是在該指令的設定裡打開
    background refresh，選單列項目就會出現並開始自行更新。

    這是最常見的「它壞掉了」回報，而且它不是這個擴充功能的 bug。

## 遠端 daemon

監看並控制另一台機器上的 `pueued`。設定只有一行——Preferences → *Remote
Connections*，填一個你本來就連得到的 SSH host：

```text
local_ubuntu
```

不需要隧道 (tunnel)、不需要 shared secret、不需要設定檔。每個指令都是以
`ssh local_ubuntu 'pueue …'` 的形式執行，並開啟 SSH 連線多工 (connection
multiplexing)：實測為**每次呼叫 10–30 ms**，相較之下*本機* pueue 是 22–44 ms，
而沒有多工的純 ssh 則是 200–400 ms。送出任務之所以能正常運作，是因為 client 跑在遠端
那台機器上，任務的工作目錄在那裡才真的解析得出來。

細節（包含進階的轉發 socket 模式）：[遠端 daemon](remote.md)。

## 接下來看哪裡

- [Getting Started](getting-started.md) — 安裝、第一個任務、鍵盤快速鍵、偏好設定。
- [遠端 daemon](remote.md) — 透過 SSH 驅動另一台主機上的 `pueued`。
- [pueue JSON 契約](pueue-json-contract.md) — `pueue` 實際上吐出什麼，以及哪些看似
  合理的解讀其實是錯的。
- [Raycast 的擴充介面](raycast-surfaces.md) — Raycast 提供哪些進入點，以及這個擴充
  功能為什麼長成現在這個樣子。
