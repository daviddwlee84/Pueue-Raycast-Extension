# Pueue Changelog

## [Initial Version] - {PR_MERGE_DATE}

- **Tasks** — browse the queue grouped by status, with a detail pane and log preview. Restart (as a new task or in place), kill, pause, stash, enqueue, remove, and clean. Accepts a pueue query as a command argument.
- **Add Task** — queue a command with a working directory, group, label, priority, dependencies, and delay.
- **Quick Add Task** — queue a command from root search, reusing your last group and directory.
- **Groups** — pause and resume groups, change parallelism, add and remove groups.
- **Queue Menu Bar** — running, queued, and failed counts in the menu bar with per-task actions, refreshing every minute.
- Live output following for running tasks, and full log viewing that reads pueue's on-disk logs directly.
- **Remote daemons** — watch and control a `pueued` on another machine. Setup is
  one line: an SSH host you can already reach. No tunnel, no shared secret, no
  config file — every command runs over ssh, multiplexed so a remote read costs
  about the same as a local one. A forwarded-socket mode remains available for
  those already maintaining a tunnel. A Connection submenu appears in Tasks and
  Groups, and a Daemon dropdown on Add Task; nothing renders when no remote
  connection is configured. Tasks submit over SSH, because pueue resolves a
  task's working directory on whichever machine the client runs on, and log reads
  never touch local disk for a connection whose logs live elsewhere. pueue's
  per-command protocol-version warning is filtered out so it never masks the real
  error underneath it.
- Actionable onboarding when the `pueue` binary can't be found or `pueued` isn't running, including a one-click daemon start when Homebrew manages it.
