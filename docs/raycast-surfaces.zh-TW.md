# Raycast 的擴充介面 (surfaces)：2026 年的現況

!!! note "Terminology rule (zh-TW pages)"
    技術名詞首次出現以「中文 (English original)」格式呈現，例：依賴注入
    (dependency injection)。**不自創翻譯**——若無公認譯名直接保留英文
    （如 `embedding`、`tokenizer`）。代碼、API 名、CLI flag、套件名、檔名一律不翻。

這份文件說明這個擴充功能為什麼長成現在這個樣子。內容是實際翻過線上官方文件後才寫的，
因為「Raycast 能不能做 X」的答案常常是「不能」，而且是那種很難從搜尋結果確認的「不能」。

## 沒有 widget API

擴充功能的進入點 (entry point) 完整清單如下：

| Entry point | Manifest | Notes |
| --- | --- | --- |
| 檢視型指令 (View command) | `"mode": "view"` | 把一個檢視 (view) 推入導覽堆疊 (navigation stack) |
| 無介面指令 (No-view command) | `"mode": "no-view"` | 執行完就結束，沒有 UI |
| **選單列指令 (Menu bar command)** | `"mode": "menu-bar"` | 回傳一個 `MenuBarExtra`。**僅限 macOS** |
| AI tools | `"tools": [...]` | 由 Raycast AI 呼叫，**Pro-gated**（需 Pro 訂閱） |
| Script commands | 獨立 repo | shell script，不是 TS 擴充功能 |

就這些。沒有桌面 widget 介面，沒有通知中心 (Notification Center) 或控制中心
(Control Center) 的擴充點，沒有 Live Activity 的對應物，也沒有浮動視窗 API。
不論是 "The New Raycast"（2026 年 5 月）還是 Glaze（2026 年 7 月），都沒有為擴充功能
推出任何 widget 形狀的東西；raycast.com 上的 "Widgets & Controls" 頁面是 **iOS 專屬**。

**所以 `mode: "menu-bar"` 指令是唯一能在不開啟 Raycast 的情況下顯示東西的方式**，
`src/queue-menu.tsx` 就是這樣一個指令。

## 選單列 (menu bar) 的限制

### 沒有 badge API

那個數字*就是* `title`，設成 `undefined` 就會消失。實測確認：沒有任何任務在跑的時候，
數字會不見，只留下圖示 (glyph)。

```tsx
title={running > 0 ? String(running) : undefined}
```

這跟 Homebrew 的 `services-menu` 用的是同一套慣用寫法 (idiom)——它是 store 裡結構上最接近的
先例，值得一讀。

### `isLoading` 是一份契約 (contract)

官方文件把這件事標成 "danger"：要嘛完全不設它——那樣 Raycast 會先算繪 (render)
然後**立刻卸載 (unload)**——要嘛在非同步 (async) 工作期間設成 `true`、做完設成 `false`。
對 `menu-bar` 指令來說，在它變成 false 之前，整棵 React tree 每一次 tick 都會重跑一遍。

### `interval` 只能寫在 manifest 裡

偏好設定 (preference) 改不了它。Raycast 會在該指令的設定畫面裡自己畫一個更新間隔的控制項。
Brew 那個擴充直接寫死 `"interval": "1m"`，也沒有提供間隔偏好設定；提供一個只會是在騙人。

官方文件在下限值上自相矛盾——背景更新 (background refresh) 那頁寫 `10s`，manifest 那頁寫
`1m`。`1m` 是安全值。

實測確認：啟動三個任務、且**沒有**從擴充功能這邊觸發任何更新，選單列的計數在 75 秒內自己更新了。

### 從 store 安裝時，背景更新預設是關的

在使用者手動跑過該指令一次、或在設定裡開啟它之前，剛裝好的選單列指令**什麼都不會顯示**。
這是最可能被回報成「它壞了」的一件事，所以它被放在 README 很前面的位置。

### 重新啟動是從資料庫還原，不是重跑指令

一個過期的算繪結果可以活過 Raycast 重啟，而且上游 (upstream) 有一些關於選單列圖示卡住的
issue 還開著。這裡的緩解做法是那一列 `Updated HH:MM`——它讓資料過期這件事被看見，
而不是繼續誤導人。

### `confirmAlert` 實務上不能用

它是在 Raycast 視窗裡跳出來的，而選單打開時那個視窗是關著的。與其冒著在破壞性操作
(destructive action) 上把確認對話框默默吞掉的風險，這裡把破壞性的選單列項目都放在 `⌥`
後面，而 `Reset` / `Remove Group` 在那邊根本不提供。

### 同層級的相同項目會誤觸

官方文件警告：同一層級下兩個一模一樣的 `MenuBarExtra.Item`，它們的 `onAction` handler 會
被接錯。這裡每一列任務都以它的 id 當前綴，讓碰撞不可能發生。

## 深層連結 (deeplink) 從外部觸發時會跳提示

`open "raycast://extensions/<author>/<ext>/<command>"` 會顯示：

> **Request to open ‹Command›** — The command was triggered from outside of
> Raycast. If you did not do this, please cancel the operation.

信任是逐一指令授予的。這件事有兩個層面的影響：它是為什麼從外部戳一下 (nudge) 沒辦法好好取代
`launchCommand`（見
[`../backlog/callback-notifications.md`](https://github.com/daviddwlee84/Pueue-Raycast-Extension/blob/main/backlog/callback-notifications.md)），
也是為什麼開發過程中有些驗證得靠真的按鍵、而不是用 deeplink。

**從擴充功能內部**呼叫 `launchCommand` 不會跳提示，而 `LaunchType.Background` 是官方文件裡
用來強制觸發同伴指令 (sibling command) 更新的方法——每一次變更操作 (mutation) 對選單列做的
就是這件事。

## 保留的快捷鍵 (shortcut)

`⌘K`（OpenActionPanel）和 `⌘P`（OpenSearchBarDropdown）是 Raycast 自己的。綁了它們會被
**默默忽略**。`ray lint` 抓得到這個問題；`tsc` 抓不到。

## `ray` 到底檢查了什麼

| Command | Checks |
| --- | --- |
| `ray build` | 用 esbuild 打包 (bundle)——**不做型別檢查 (typecheck)** |
| `ray lint` | ESLint + Prettier + manifest + icons——不做型別檢查 |
| `tsc --noEmit` | 型別 |

三個都需要。見
[`../pitfalls/ray-build-does-not-typecheck.md`](https://github.com/daviddwlee84/Pueue-Raycast-Extension/blob/main/pitfalls/ray-build-does-not-typecheck.md)。
