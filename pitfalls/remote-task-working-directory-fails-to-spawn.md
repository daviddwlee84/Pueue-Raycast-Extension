# A remote task ends as FailedToSpawn and never runs

## Symptoms (grep this section)

```text
FailedToSpawn
{"FailedToSpawn": "No such file or directory (os error 2)"}
Failed to canonicalize given working directory path
```

A task submitted to a **remote** daemon finishes instantly having produced
nothing — or the client refuses to submit it at all.

## Cause

pueue records a task's working directory at submit time and **canonicalises it
on whichever machine the client runs on**. Against a remote daemon that is the
wrong machine, and it fails in three distinct ways:

| Submitted with | Result |
|---|---|
| a local directory that exists here | The local path is sent; it doesn't exist on the daemon → `FailedToSpawn` |
| a remote-only path | The **local** client refuses: `Failed to canonicalize given working directory path` |
| `/tmp`, from macOS | Silently rewritten to `/private/tmp` (a macOS symlink) → fails on Linux |

Only a path existing *and canonicalising identically on both machines* works —
`/usr` does, your project directory does not. This makes cross-platform remote
submission impractical by the obvious route.

## Fix

Give the connection an SSH host, and submission runs the client on the far side
where the paths are real:

```text
gpu-box | ~/.config/pueue/remote/client.yml | myhost
```

Add Task then submits with `ssh myhost 'pueue add …'`. Reads and control
commands still use the forwarded socket — SSH is only for submission, because
submission is the only operation that resolves a path.

Without an SSH host the working-directory field says so rather than letting you
submit a job that can only fail.

## Two things this drags along

1. **The whole command has to survive one more shell.** `ssh host '<cmd>'` hands
   its argument to the *remote* shell, so everything is POSIX single-quoted
   (`'\''` for embedded quotes) in `src/lib/pueue/ssh.ts`. This is separate from
   `argvFor`, which is deliberately quote-free because `execFile` needs none.
2. **`FailedToSpawn` is `Done`, not `Failed`,** and its result is dict-shaped
   rather than a bare string. A denylist of failure results misses it and reports
   a job that never started as a success — which is why `isFailed` is an
   allowlist of "terminal and not Success".

## Related

- [`../docs/remote.md`](../docs/remote.md) — full setup
- `read_local_logs: false` is a separate hard requirement; without it a remote
  task's log read returns a *local* task's output under the same id.
