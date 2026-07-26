# Getting started

A first run, start to finish: install pueue, install the extension, queue one
task, put the queue in the menu bar, and read a failure.

The extension shells out to the `pueue` command line tool. It queues nothing
itself, so step 1 is not optional.

## 1. Install pueue and start the daemon

```sh
brew install pueue
# or
cargo install --locked pueue
```

Then start the daemon:

```sh
brew services start pueue
```

or, **from a terminal**:

```sh
pueued -d
```

!!! warning "Start the daemon from a terminal or `brew services`, not from a GUI app"

    `pueued` hands its own environment to **every task it ever runs**. Started as
    a child of a GUI process, that environment has no `~/.zshrc` and a bare
    `PATH`, and every task you queue inherits it. The extension's one-click
    *Start Daemon* action only appears when `brew services` manages the daemon,
    so launchd owns it rather than Raycast.

Verify both halves — the client and the daemon — with one command:

```sh
pueue status
```

A working install prints a table (empty is fine, `Task list is empty` is fine).
If the daemon is not up you get an error instead; see
[troubleshooting](#6-troubleshooting) below.

Check the version while you are here:

```sh
pueue --version
```

!!! note "pueue 4.x only"

    Pueue 4.0 changed the state format incompatibly — task timestamps and
    results moved inside the status enum — so pueue 3.x is not supported.

## 2. Install the extension

The extension is **not on the Raycast store yet**. For now, run it from source:

```sh
git clone https://github.com/daviddwlee84/Pueue-Raycast-Extension.git
cd Pueue-Raycast-Extension
npm install
npm run dev
```

`npm run dev` (`ray develop`) registers the extension in Raycast's root search.
It stays registered after you stop the dev process with `Ctrl-C`, so you can use
it normally from then on — `npm run dev` is only needed again to pick up code
changes.

Open Raycast and type `Pueue`. Five commands should appear:

| Command | What it does |
| --- | --- |
| **Tasks** | Browse the queue grouped by status, with a detail pane and log preview. Takes an optional pueue query as an argument, e.g. `status=failed order_by id desc`. |
| **Add Task** | Queue a command with a working directory, group, label, priority, dependencies, and delay. |
| **Quick Add Task** | Queue a command straight from root search, reusing the group and directory you last used. |
| **Groups** | Pause and resume groups, change parallelism, add and remove groups. |
| **Queue Menu Bar** | Running / queued / failed counts in the menu bar, with per-task actions. |

## 3. Queue your first task

Open Raycast, type `Quick Add Task`, press `Tab` to move into the argument, and
paste something that takes a few seconds so you can watch it run:

```sh
sleep 20 && echo done
```

Press `⏎`. A HUD confirms `Queued task 0 · sleep 20 && echo done`. There is no
window — **Quick Add Task** is a `no-view` command that runs and exits.

On a first run it queues into the `default` group with your home directory as
the working directory. After that it reuses whatever **Add Task** last used, so
the common case needs no configuration. Use **Add Task** when you want to pick
the directory, group, label, priority, dependencies, or a delay.

Now open **Tasks**. The new task appears under *Running*, with the command, its
group, and how long it has been going. Selecting it fills the detail pane with
metadata and the last 20 log lines.

!!! note "Tasks run in the daemon's environment"

    pueue runs each command as `sh -c '<your command>'` in **pueued's**
    environment, not your shell's. A command that relies on `PATH` entries from
    `~/.zshrc` may need an absolute path, or an entry under `daemon.env_vars` in
    `pueue.yml`. A trailing `&` detaches the process, so the task finishes
    instantly while the real work keeps running unsupervised.

## 4. Turn on the menu bar

Run **Queue Menu Bar** from Raycast's root search **once**.

!!! warning "A fresh install shows nothing until you run the command once"

    Raycast disables background refresh for store installs until the command is
    first opened. Until then the menu bar item does not exist. Run it once, or
    enable background refresh in that command's settings, and it appears and
    starts updating on its own every minute.

    This is the single most likely "it's broken" report, and it is not a bug in
    the extension.

Once it is running you get a pueue glyph in the menu bar with a number next to
it. By default that number is the **running** count. The command's settings
change what it counts:

| Menu Bar Title | Shows |
| --- | --- |
| Running count *(default)* | running |
| Running / queued | both, separated |
| Running + queued + paused | one total |
| Icon only | no number at all |

**The title disappears at zero.** Raycast has no badge API — the count *is* the
title, and an empty title renders as just the glyph. So an idle queue is a bare
icon, not a `0`.

Open the menu to see Running, Queued, and Failed sections with per-task actions,
a Groups section with pause/resume and a parallelism picker, and an
`Updated HH:MM` row. That last row exists because Raycast restores a menu bar
render from a database rather than by re-running the command, so a stale render
can outlive a restart — the timestamp makes staleness visible instead of
misleading.

!!! tip "Destructive actions in the menu bar are behind `⌥`"

    Raycast's confirmation dialog presents in the Raycast window, which is
    closed while a menu is open. Rather than risk a silently swallowed
    confirmation, kill and remove only appear when you hold `⌥`, regardless of
    the *Confirm destructive actions* preference.

If your queue holds thousands of finished tasks, set **Menu Bar Query** to
`last 100` so the minute-by-minute read stays cheap.

## 5. Reading a failure

Queue something that fails:

```sh
ls /definitely-not-a-directory
```

Open **Tasks**. It lands under *Failed* with its exit code.

**The detail pane** (`⌘⇧D` toggles it) shows the command, working directory,
group, label, timestamps, exit code, and a preview of the last 20 log lines —
enough to identify most failures without leaving the list. Raise or lower the
preview length with the *Log Preview Lines* preference on the Tasks command;
each selection change costs one extra `pueue log` call.

**The log view** (`⏎`) shows the full output, read from pueue's `task_logs/`
directory on disk. `⌘⇧F` copies `pueue follow <id>` if you would rather watch it
in a terminal.

**Following live output** (`⌘L`) works on running tasks: it streams `pueue
follow`, which polls the on-disk log every 250 ms. The header reads *Following*
while the task runs and flips to *Finished* when it ends — `follow` exiting is
the task finishing, not an error. Leaving the view kills the child process.

The rest of the Tasks shortcuts:

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

The usual loop on a failure is: read the log, fix the command, `⌘⇧R` to restart
as a new task. Use `⌘⌥R` only when you want to keep the id — it overwrites the
existing log.

To see only failures, pass a query as the command's argument:

```text
status=failed order_by id desc
```

## 6. Troubleshooting

### "It works in my terminal but the extension can't find pueue"

Symptom: **Pueue CLI not found**, or `spawn pueue ENOENT`, while `which pueue`
answers instantly in your terminal.

Raycast runs extensions under launchd, which never sources `~/.zshrc`,
`~/.zprofile`, or any other shell rc. Homebrew's `bin` and `~/.cargo/bin` are
added to `PATH` *by those files*, so the extension's `PATH` is roughly
`/usr/bin:/bin` and a bare `pueue` cannot be found.

The extension never invokes a bare binary name. It probes, in order:

```text
/opt/homebrew/bin   Apple Silicon Homebrew
/usr/local/bin      Intel Homebrew
~/.cargo/bin
~/.local/bin
```

If yours lives somewhere else, find it and set it explicitly:

```sh
which pueue
```

Paste the result into Raycast → Extensions → Pueue → **Pueue Binary Path**.

!!! note "The dev terminal hides this class of bug"

    `npm run dev`'s console inherits your full interactive `PATH`, so a
    PATH-related problem can work there and fail in production. Exercise
    PATH changes from Raycast — root search or the menu bar — not from the
    dev terminal. Background:
    [pitfalls/raycast-launchd-path-pueue-not-found.md](https://github.com/daviddwlee84/Pueue-Raycast-Extension/blob/main/pitfalls/raycast-launchd-path-pueue-not-found.md).

### "Pueue daemon not running"

pueue is installed but `pueued` isn't reachable. The extension says so with a
one-click **Start Daemon (brew services)** action — but only when `brew
services` already manages the daemon, because starting `pueued` from Raycast
would make it a child of Raycast's launchd process and hand that stripped
environment to every task it later runs.

Otherwise, start it yourself and reload with `⌘R`:

```sh
brew services start pueue
# or
pueued -d
```

Two error strings mean different things:

- `Did you start the daemon at least once?` — `pueued` has **never** run, so
  there is no shared secret file yet.
- `while connecting to daemon` — it ran and has since stopped.

## Next steps

- [Remote daemons](remote.md) — watch and control a `pueued` on another machine.
  Setup is one entry in the **Remote Connections** preference: an SSH host you
  can already reach, e.g. `local_ubuntu`. No tunnel, no shared secret, no config
  file. A second remote goes in the same field after a `;` — Raycast preferences
  are single-line textfields, so a semicolon does the job a newline can't.
  Multiplexed SSH measures **10–30 ms per call** against 22–44 ms for a
  local pueue (200–400 ms without multiplexing, which is why the extension always
  passes `ControlMaster`). Submitting works because the client runs on the remote
  box, where the working directory actually resolves.
- [The pueue JSON contract](pueue-json-contract.md) — what the extension parses
  out of `pueue status --json` and `pueue log --json`.
- [Raycast surfaces](raycast-surfaces.md) — why the menu bar is the only
  always-visible surface. Raycast has **no widget API** for extensions; a
  `mode: "menu-bar"` command is the whole of it.
- Desktop notifications when a task finishes are a pueue feature, not an
  extension one — this extension never writes to your `pueue.yml`. The
  `daemon.callback` snippet is in the
  [README](https://github.com/daviddwlee84/Pueue-Raycast-Extension/blob/main/README.md#notifications-when-a-task-finishes).
- [TODO.md](https://github.com/daviddwlee84/Pueue-Raycast-Extension/blob/main/TODO.md)
  — what is planned next.
