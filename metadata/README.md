# Store screenshots

Raycast requires **3–6 PNGs at 2000×1250** (16:10) here before publishing.

Use Raycast's **Window Capture**, which writes correctly-sized files straight
into this folder — don't use `screencapture`, which photographs your whole
desktop.

1. Give Window Capture a hotkey: Raycast Settings → Advanced → Window Capture
   (the docs suggest `⌘⇧⌥M`), or set a hotkey on the **Capture Window** command.
2. Open the extension command you want to shoot, under `just dev`.
3. Press the hotkey, **tick "Save to Metadata"**, then click the camera button.

The "Save to Metadata" option only appears when a `metadata` folder already
exists — it does, because of this file.

Suggested set:

1. **Tasks** — the list with several statuses (running, stashed, failed)
2. **Tasks** — the detail pane open on a failed task, showing the exit code and log
3. **Add Task** — the form with the advanced options revealed (⌘⇧A)
4. **Groups** — two or three groups with different parallelism and pause states
5. **Queue Menu Bar** — the menu open, showing the Running / Failed sections

`just fixtures` seeds a queue that covers most of these states.

This directory is the last unchecked item in
[`../backlog/store-publishing.md`](../backlog/store-publishing.md).
