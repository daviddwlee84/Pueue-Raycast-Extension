# Pueue Changelog

## [Initial Version] - {PR_MERGE_DATE}

- **Tasks** — browse the queue grouped by status, with a detail pane and log preview. Restart (as a new task or in place), kill, pause, stash, enqueue, remove, and clean. Accepts a pueue query as a command argument.
- **Add Task** — queue a command with a working directory, group, label, priority, dependencies, and delay.
- **Quick Add Task** — queue a command from root search, reusing your last group and directory.
- **Groups** — pause and resume groups, change parallelism, add and remove groups.
- **Queue Menu Bar** — running, queued, and failed counts in the menu bar with per-task actions, refreshing every minute.
- Live output following for running tasks, and full log viewing that reads pueue's on-disk logs directly.
- Actionable onboarding when the `pueue` binary can't be found or `pueued` isn't running, including a one-click daemon start when Homebrew manages it.
