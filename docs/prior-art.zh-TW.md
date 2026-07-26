# 相關擴充

Raycast Store 上解決同一類問題的擴充：**包一個使用者自己安裝的 CLI 或 daemon，
讓它的狀態不必打開終端機就看得到。** 之所以收集起來，是因為每一個都替某個
原本只能靠意見決定的問題給出了實例。

## 需要預先安裝二進位檔的擴充

Store 指南一方面說「避免要求使用者額外下載」，另一方面又明確允許
「✅ 呼叫已知的系統二進位檔」。下面四個就是後者為真的證據：

| 擴充 | 需要 | 它證明了什麼 |
| --- | --- | --- |
| [Brew](https://www.raycast.com/nhojb/brew) | `brew` | 結構上最接近的先例。它的 `services-menu` 是一個 `interval: 1m` 的 `menu-bar` command（對照過[它的 manifest](https://github.com/raycast/extensions/blob/main/extensions/brew/package.json)），[`raycast-surfaces.md`](raycast-surfaces.zh-TW.md) 裡「用 title 當 badge」的慣用法就是讀它來的。 |
| [Colima](https://www.raycast.com/MiskaMyasa/colima) | `colima`、Docker CLI | 一個用 Homebrew 安裝的容器 runtime，整個擴充都靠 shell out 驅動。 |
| [OrbStack](https://www.raycast.com/nicholasq/orbstack) | `orbctl` / `orb` | 同上，但對象是商業 App 的 CLI。 |
| [Yabai](https://www.raycast.com/krzysztoff1/yabai) | `yabai` | 不只需要二進位檔，還需要一個**正在跑的 daemon** —— 而且它文件裡有一個 `yabai` signal，用來戳 Raycast 的背景 command 更新 menu bar 指示。 |

審核者要看的不是「零依賴」，而是**優雅降級**：
在二進位檔不存在時吐出原始錯誤物件的擴充看起來就是壞的；
把問題解釋清楚、順手給出安裝指令的擴充看起來就是完成品。
[`src/lib/error-states.tsx`](https://github.com/daviddwlee84/Pueue-Raycast-Extension/blob/main/src/lib/error-states.tsx)
就是為了這件事存在的。

## macOS 的 job 與服務管理

| 擴充 | 涵蓋範圍 | 備註 |
| --- | --- | --- |
| [Launch Agents](https://www.raycast.com/stevensd2m/launch-agents) | 列出、載入、卸載、移除 `~/Library/LaunchAgents` 條目 —— 1,214 installs | 目前 launchd 這一塊的現況。它做的是管理，不是觀測：沒有 exit status、沒有下次觸發時間、沒有 log tail。為什麼那個缺口才是有意思的部分、以及為什麼補它比看起來難，見 [`backlog/launchd-jobs-extension.md`](https://github.com/daviddwlee84/Pueue-Raycast-Extension/blob/main/backlog/launchd-jobs-extension.md)。 |

更廣的「哪種工作該用哪個工具」——`pueue` 對 `systemd-run --user` 對 `nohup`
對 `tmux`——見[這篇比較筆記](https://gist.github.com/daviddwlee84/35a2aa5d477c99c8615a6232f1a1f308)。

## 擴充開發本身的工具

| 擴充 | 做什麼 | 為什麼列在這裡 |
| --- | --- | --- |
| [Capture Raycast Metadata](https://www.raycast.com/koinzhang/capture-raycast-metadata) | 為 `metadata/` 截 Raycast 視窗 —— 819 installs | 一個現成的例子，說明**為什麼上架截圖沒辦法自動化**。它驅動 ScreenCapture App，只有在螢幕實際解析度剛好是 UI 縮放兩倍時才會產出正確尺寸；它自己的頁面就寫「目前只在某些解析度下正常運作」。勾了「Save to Metadata」的 Raycast 內建 Window Capture 仍然是唯一可靠的路。 |

## 參考資料

- [Prepare an Extension for Store](https://developers.raycast.com/basics/prepare-an-extension-for-store)
  —— 檢查清單，也是 2000×1250 截圖規格的出處。
- [Menu Bar Commands](https://developers.raycast.com/api-reference/menu-bar-commands)
  —— 包含 [`raycast-surfaces.md`](raycast-surfaces.zh-TW.md) 引用的那條標為
  「danger」的 `isLoading` 說明。
- [raycast/extensions](https://github.com/raycast/extensions) —— 所有已發佈擴充的原始碼。
  想知道某件事在 Raycast 到底做不做得到，去讀一個解決相鄰問題的已上架擴充，
  一向比在文件裡搜尋快。
- 這裡學到的一切，通用化之後放在
  [`raycast-extension-dev`](https://daviddwlee84.github.io/agent-skills/skills/raycast-extension-dev/)
  agent skill 裡。
