# Pueue for Raycast

A Raycast extension for the [Pueue](https://github.com/Nukesor/pueue) task queue
daemon: browse and act on tasks, follow live output, control groups and
parallelism, and watch the queue from the menu bar. It shells out to the `pueue`
command line tool and queues nothing itself, so `pueue` has to be installed and
`pueued` has to be running before any of it does anything.

## Commands

| Command | What it does |
| --- | --- |
| **Tasks** | Browse the queue grouped by status, with a detail pane and log preview. Restart, kill, pause, stash, enqueue, remove, and clean. Takes an optional pueue query as an argument, e.g. `status=failed order_by id desc`. |
| **Add Task** | Queue a command with a working directory, group, label, priority, dependencies, and delay. |
| **Quick Add Task** | Queue a command straight from root search, reusing the group and directory you last used. |
| **Groups** | Pause and resume groups, change parallelism, add and remove groups. |
| **Queue Menu Bar** | Running / queued / failed counts in the menu bar, with per-task actions. Refreshes every minute. |

Raycast has no widget API. A `mode: "menu-bar"` command is the only
always-visible surface available to an extension, which is what **Queue Menu
Bar** is — see [Raycast surfaces](raycast-surfaces.md).

## Requirements

Install the CLI:

```sh
brew install pueue
# or
cargo install --locked pueue
```

Start the daemon:

```sh
brew services start pueue
```

or, from a terminal:

```sh
pueued -d
```

!!! warning "Start the daemon from a terminal or `brew services`, not from a GUI app"

    A `pueued` started as a child of a GUI process inherits that process's
    environment — no `~/.zshrc`, a bare `PATH` — and hands it to **every task it
    ever runs**. The extension's one-click *Start Daemon* action only appears
    when `pueued` is managed by `brew services`, so launchd owns it.

Built for **pueue 4.x**. Pueue 4.0 changed the state format incompatibly — task
timestamps and results moved inside the status enum — so pueue 3.x is not
supported.

!!! note "The extension can't use a bare `pueue`"

    Raycast runs extensions under launchd, which never sources your shell rc, so
    Homebrew and `~/.cargo/bin` are not on its `PATH` even though they are in
    your terminal. The extension always invokes an absolute path and probes
    `/opt/homebrew/bin`, `/usr/local/bin`, `~/.cargo/bin`, `~/.local/bin`. If
    yours lives elsewhere, set **Pueue Binary Path**.

## Background refresh is off by default

!!! warning "A freshly installed menu bar command shows nothing until you run it once"

    Raycast disables background refresh for store installs until the command is
    first opened. Run **Queue Menu Bar** from Raycast's root search once, or
    enable background refresh in that command's settings, and the menu bar item
    appears and starts updating on its own.

    This is the single most likely "it's broken" report, and it isn't a bug in
    the extension.

## Remote daemons

Watch and control a `pueued` on another machine. Setup is one line —
Preferences → *Remote Connections*, an SSH host you can already reach:

```text
local_ubuntu
```

No tunnel, no shared secret, no config file. Every command runs as
`ssh local_ubuntu 'pueue …'`, with SSH connection multiplexing on: measured at
**10–30 ms per call**, against 22–44 ms for a *local* pueue, and 200–400 ms for
plain ssh without multiplexing. Submitting works because the client runs on the
remote box, where a task's working directory actually resolves.

Details, including the advanced forwarded-socket mode:
[Remote daemons](remote.md).

## Where to next

- [Getting Started](getting-started.md) — install, first task, keyboard
  shortcuts, preferences.
- [Remote daemons](remote.md) — driving a `pueued` on another host over SSH.
- [The pueue JSON contract](pueue-json-contract.md) — what `pueue` actually
  emits, and where a reasonable reading of it is wrong.
- [Raycast surfaces](raycast-surfaces.md) — the entry points Raycast offers, and
  why this extension is shaped the way it is.
