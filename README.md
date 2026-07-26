# Pueue for Raycast

Manage the [Pueue](https://github.com/Nukesor/pueue) task queue daemon from Raycast: browse and act on tasks, follow live output, control groups and parallelism, and watch the queue from the menu bar.

## Requirements

This extension drives the `pueue` command line tool. It does not queue anything itself.

```sh
brew install pueue
# or
cargo install --locked pueue
```

### Start the daemon

```sh
brew services start pueue
```

or, **from a terminal**:

```sh
pueued -d
```

> **Start the daemon from a terminal or `brew services`, not from a GUI app.**
> A `pueued` started as a child of a GUI process inherits that process's
> environment — no `~/.zshrc`, a bare `PATH` — and hands it to **every task it
> ever runs**. The extension's one-click *Start Daemon* action only appears when
> `pueued` is managed by `brew services`, so launchd owns it.

## Background refresh is off by default

**A freshly installed menu bar command shows nothing until you run it once.**

Raycast disables background refresh for store installs until the command is first opened. Run **Queue Menu Bar** from Raycast's root search once, or enable background refresh in that command's settings, and the menu bar item appears and starts updating on its own.

This is the single most likely "it's broken" report, and it isn't a bug in the extension.

## Commands

| Command | What it does |
| --- | --- |
| **Tasks** | Browse the queue grouped by status, with a detail pane and log preview. Restart, kill, pause, stash, enqueue, remove, and clean. Takes an optional pueue query as an argument, e.g. `status=failed order_by id desc`. |
| **Add Task** | Queue a command with a working directory, group, label, priority, dependencies, and delay. |
| **Quick Add Task** | Queue a command straight from root search, reusing the group and directory you last used. |
| **Groups** | Pause and resume groups, change parallelism, add and remove groups. |
| **Queue Menu Bar** | Running / queued / failed counts in the menu bar, with per-task actions. Refreshes every minute. |

### Keyboard shortcuts (Tasks)

| Shortcut | Action |
| --- | --- |
| `⏎` | Show log |
| `⌘L` | Follow output (running tasks) |
| `⌘⇧R` | Restart as a new task |
| `⌘⌥R` | Restart in place — same id, **overwrites the log** |
| `⌘⇧P` | Pause · `⌘⇧S` Resume / Start now |
| `⌘⇧T` | Stash · `⌘⇧E` Enqueue |
| `⌘⇧K` | Kill · `⌘⌫` Remove |
| `⌘⇧D` | Toggle the detail pane · `⌘R` Reload |

## Preferences

| Preference | Default | Notes |
| --- | --- | --- |
| **Pueue Binary Path** | auto-probe | Empty probes `/opt/homebrew/bin`, `/usr/local/bin`, `~/.cargo/bin`, `~/.local/bin`. |
| **Pueue Config Path** | pueue's default | Passed as `--config`, and used to locate `task_logs/`. |
| **Confirm destructive actions** | on | Kill, remove, clean, and reset ask first. Menu bar destructive actions are always behind `⌥`. |

### "It works in my terminal but the extension can't find pueue"

Raycast runs extensions under launchd, which never sources your shell rc — so Homebrew and `~/.cargo/bin` are **not** on its `PATH` even though they are in your terminal. The extension always invokes an absolute path and probes the directories above. If yours lives somewhere else, set **Pueue Binary Path**.

## Tasks inherit the daemon's environment

pueue runs each command as `sh -c '<your command>'` in the **daemon's** environment, not your shell's. A command that depends on `$PATH` entries from `~/.zshrc` may need an absolute path, or an entry under `daemon.env_vars` in `pueue.yml`.

A trailing `&` detaches the process, so the task finishes instantly while the real work keeps running unsupervised.

## Notifications when a task finishes

Pueue can run a command whenever a task completes. **This extension never writes to your `pueue.yml`** — add this yourself:

```yaml
daemon:
  callback: 'osascript -e "display notification \"{{ command }} → {{ result }}\" with title \"Pueue #{{ id }}\""'
  callback_log_lines: 10
```

Available variables: `id`, `command`, `path`, `group`, `result`, `exit_code`, `start`, `end`, `output`, `output_path`, `queued_count`, `stashed_count`. Restart the daemon after editing the config.

## Compatibility

Built for **pueue 4.x**. Pueue 4.0 changed the state format incompatibly — task timestamps and results moved inside the status enum — so pueue 3.x is not supported.

## License

MIT
