# Store screenshots

Raycast requires **3–6 PNGs at 2000×1250** here before publishing.

Capture them with Raycast's own **Save Screenshot** action while the extension
is running under `just dev` — open the command, then use the action from the
Action Panel. It frames the window correctly and excludes the rest of your
desktop, which a plain `screencapture` does not.

Suggested set:

1. **Tasks** — the list with several statuses (running, stashed, failed)
2. **Tasks** — the detail pane open on a failed task, showing the exit code and log
3. **Add Task** — the form with the advanced options revealed (⌘⇧A)
4. **Groups** — two or three groups with different parallelism and pause states
5. **Queue Menu Bar** — the menu open, showing the Running / Failed sections

`just fixtures` seeds a queue that covers most of these states.

This directory is the last unchecked item in
[`../backlog/store-publishing.md`](../backlog/store-publishing.md).
