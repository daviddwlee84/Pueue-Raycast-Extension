# The pueue JSON contract

What `pueue` v4.0.4 actually emits, and every place a reasonable reading of it
is wrong. Everything here was captured from the real binary, not from
documentation — the assertions in `src/lib/dev-check.ts` are the executable
version of this file.

## Only three commands emit JSON

`status`, `log`, and `group`. Every one of the fifteen mutations is exit code
plus prose. That is why `PueueTransport.mutate` returns `void`, and why `add`
has to be coaxed into usefulness with `--print-task-id`.

## Global flags precede the subcommand

```console
$ pueue status --color never --json
error: unexpected argument '--color' found
$ echo $?
2
```

```console
$ pueue --color never status --json
{"tasks":{},"groups":{"default":{"status":"Running","parallel_tasks":1}}}
```

**Exit codes are 0, 1, and 2** — not 0/1. Clap argument errors are 2, and a
mis-built argv surfaces as one.

## `status --json`

```jsonc
{
  "tasks":  { "<id>": { /* Task */ } },   // keys are strings; id also appears inside
  "groups": { "<name>": { "status": "Running"|"Paused"|"Reset", "parallel_tasks": 0 } }
}
```

`parallel_tasks: 0` means unlimited.

### `group --json` is a different shape

It returns the **inner map only** — `{"default": {...}}`, not `{"groups": {...}}`.
Sharing a parser between the two is the easiest bug in this codebase, so
`readState` explicitly guards against being handed the wrong one.

### `--group` filters tasks but not groups

`status --json --group X` filters the `tasks` map and leaves `groups` complete.
A nonexistent group name silently succeeds.

## `status` is a two-level externally-tagged enum

There is no flat `"status": "Running"` anywhere.

```jsonc
{"Stashed": {"enqueue_at": null}}
{"Stashed": {"enqueue_at": "2026-07-27T03:00:00.000000+08:00"}}
{"Queued":  {"enqueued_at": "..."}}
{"Running": {"enqueued_at": "...", "start": "..."}}
{"Paused":  {"enqueued_at": "...", "start": "..."}}
{"Locked":  {"previous_status": { /* recursive TaskStatus */ }}}
{"Done":    {"enqueued_at": "...", "start": "...", "end": "...", "result": <TaskResult>}}
```

- **`Locked` is recursive.** It wraps the status the task returns to when the
  edit finishes. Filter on `underlyingKind()`, which unwraps.
- **`enqueue_at` vs `enqueued_at`.** `Stashed` uses the first, nullable, and it
  points *forwards* — when the task *will* be enqueued. Every other variant uses
  the second, pointing backwards.
- **`new Date(null)` is 1970-01-01, not `Invalid Date`.** An unguarded parse of
  a plain stashed task renders as 1970. Hence `parseTs`.

### `TaskResult` mixes bare strings with objects

| JSON | Meaning |
| --- | --- |
| `"Success"` | exited 0 |
| `{"Failed": 127}` | non-zero exit; the number is the code |
| `{"FailedToSpawn": "..."}` | couldn't spawn at all — bad cwd, unreadable binary |
| `"Killed"` | killed by you or by daemon shutdown |
| `"Errored"` | internal IO error |
| `"DependencyFailed"` | an `--after` parent failed; never ran |

**Failure detection is an allowlist**: anything terminal that isn't `"Success"`.
A denylist would miss whichever variant pueue adds next.

Note that a command-not-found does **not** produce `FailedToSpawn` — pueue runs
through `sh -c`, so the shell returns 127 and you get `{"Failed": 127}`.

### Timestamps

chrono `DateTime<Local>` → RFC 3339 with **microseconds** and a **numeric
offset**: `2026-04-27T11:01:06.893055+08:00`. Never `Z`, never epoch. V8 parses
the extra fractional digits and truncates to milliseconds.

## `envs` is a full environment snapshot

Present on every task in `status --json`, blanked to `{}` in `log --json`.
Measured on this machine: **six trivial tasks weigh 53,595 bytes with it and
2,509 without — 21×, at 120 variables per task.**

It may contain secrets, and `useCachedPromise` persists to Raycast's disk-backed
cache. The transport strips it at the parse boundary; `taskEnvs(id)` re-reads on
demand for the one place that wants it.

## `log --json` hides its own errors in the output

```console
$ pueue log 1 --json          # task 1 has never run
{"1":{"task":{...},"output":"(Pueue error) Failed to get log file handle: I/O error at path
\"…/task_logs/1.log\" while getting log file handle:\nNo such file or directory (os error 2)"}}
$ echo $?
0
```

Exit code 0, with pueue's error text sitting in the `output` field where the
task's own output belongs. There is no signal to branch on, so
`cleanLogOutput()` detects the `(Pueue error)` prefix, and `hasEverRun()` avoids
asking in the first place.

`pueue log <unknown-id> --json` is `{}` with exit 0 — empty, not an error.

## Errors are prose on stderr, with unsuppressable ANSI

`color_eyre` writes SGR escapes to stderr **even when stderr is a pipe**.
Neither `--color never` nor `NO_COLOR=1` suppresses them. Seven captured
failure shapes live in `src/lib/fixtures/stderr.json`; the two that matter:

```text
# pueued has NEVER run — no shared secret file yet
I/O error at path "…/shared_secret" while opening secret file.
Did you start the daemon at least once?

# pueued ran and has since stopped (ENOENT, or ECONNREFUSED if the socket remains)
I/O error at path "…/pueue_<user>.socket" while connecting to daemon. Did you start it?
```

Both classify as `daemon-not-running` because the remedy is identical, and the
raw detail is kept so the UI can still tell the user which happened.

## There is no push, and the daemon lags its own acknowledgement

`pueue wait` polls internally every 2 s and prints prose. `pueue follow` streams
log text (verified: lines arrive at the task's own cadence), but never state.
State must be polled — `status --json` costs 22–44 ms, median 28 ms.

And the daemon **acks a request before its update loop applies it**. Killing a
running task and polling until `status --json` reports `Done`, five trials:

```text
min 278 ms   median 284 ms   max 297 ms
```

The ack itself returns in ~22 ms. This is why every mutation suppresses
`shouldRevalidateAfter` and reconciles on a delay instead — see
`src/lib/actions.tsx`.

## Behaviours the verb doesn't tell you

| Command | Also does |
| --- | --- |
| `kill --group X` / `kill --all` | **pauses** the group(s) |
| `group remove X` | **moves** X's tasks to `default` rather than deleting them |
| `remove <id>` | refuses running or paused tasks; kill them first |
| `restart --in-place` | reuses the id and **overwrites the existing log** |
| `restart --not-in-place` | mints a new task id (verified: ids `[1,2]` → `[1,2,3]`) |
| `add --escape` | escapes metacharacters **including spaces** — with the command passed as one argv element this collapses it into a single token, so it is not exposed in the UI |
| `parallel` with no argument | broken in 4.0.4: logs "Received unhandled response message", exits 0, prints nothing. Read parallelism from `group --json`. |

## The query DSL

Applied by the pueue **client**, so it shrinks what we parse rather than what the
daemon sends. Composes with `--json`.

```text
[columns=id,status,…] [filter]* [order_by <column> asc|desc] [first|last N]

filter columns   status | command | label | start | end | enqueue_at
operators        =  !=  <  >  %=       (%= means "contains")
status values    queued | stashed | paused | running | success | failed
```

The brackets in pueue's own grammar documentation are meta-notation:
`columns=[id]` fails to parse, `columns=id,status` works.

## Command escaping

`pueue add` takes a variadic `<COMMAND>...`, joins it with spaces, and hands the
result to `sh -c`. The extension passes the whole command as **one argv element
after `--`** and never quotes it — quoting here would double-escape. Verified:

```console
$ # via argvFor: ["add","--print-task-id","--","echo \"double\" && echo 'single' && echo HOME=$HOME"]
double
single
HOME=/Users/david
```
