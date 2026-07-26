# `pueue` not found from Raycast, but works in the terminal

## Symptoms (grep this section)

```text
Error: spawn pueue ENOENT
pueue CLI not found
```

The extension shows **"Pueue CLI not found"** while `which pueue` in your
terminal answers immediately.

## Cause

Raycast runs extensions in a managed Node process under **launchd**, which never
sources `~/.zshrc`, `~/.zprofile`, or any other shell rc. Homebrew's `bin` and
`~/.cargo/bin` are added to `PATH` *by those files*, so the extension's `PATH` is
roughly `/usr/bin:/bin` and a bare `pueue` cannot be found.

## Fix

Never invoke a bare binary name. `src/lib/pueue/binary.ts` resolves an absolute
path: the `pueuePath` preference first (validated with `existsSync`, so a stale
value falls through), then a probe of

```text
/opt/homebrew/bin   Apple Silicon Homebrew
/usr/local/bin      Intel Homebrew — and this machine, where /opt/homebrew does not exist
~/.cargo/bin
~/.local/bin
/usr/bin  /bin
```

Both Homebrew prefixes must be probed. Hardcoding either breaks half of all Macs.

## The part that makes this expensive to find

**A terminal hides the bug completely.** `npm run dev`'s console inherits your
full interactive `PATH`, so anything you test from there works, including a
`resolveBinary` that would fail in production.

Every PATH-related change must be exercised **from Raycast** — root search or the
menu bar — not from the dev terminal.

## Related

The same launchd environment problem, one level out: a `pueued` started as a
child of a GUI process hands its stripped environment to every task it ever runs.
That's why the one-click *Start Daemon* action only appears when `brew services`
manages the daemon — see `src/lib/error-states.tsx`.
