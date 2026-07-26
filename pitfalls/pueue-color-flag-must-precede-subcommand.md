# `error: unexpected argument '--color' found`, exit code 2

## Symptoms (grep this section)

```text
error: unexpected argument '--color' found

  tip: to pass '--color' as a value, use '-- --color'

Usage: pueue status [OPTIONS] [QUERY]...
```

Exit code **2**, and the extension reports a generic "Pueue command failed".

## Cause

`--color`, `--config`, `--profile`, and `--verbose` are **global** options on
`pueue`, not per-subcommand ones. Clap rejects them after the subcommand.

```console
$ pueue status --color never --json    # exit 2
$ pueue --color never status --json    # exit 0
```

## Fix

`globalArgs()` in `src/lib/pueue/cli-transport.ts` is prepended, never appended:

```ts
const argv = [...globalArgs(), ...args];
```

## Two things this teaches beyond the flag itself

1. **Exit codes are 0, 1, and 2** — not 0/1 as pueue's own behaviour otherwise
   suggests. `classify()` treats exit 2 with a leading `error:` as
   `bad-arguments`, which is always an extension bug and is surfaced verbatim
   rather than dressed up.
2. `--color never` does **nothing** for stderr. Error reports come from
   `color_eyre`, which writes SGR escapes even to a pipe and ignores both
   `--color` and `NO_COLOR`. The flag is still worth passing because it cleans
   up the prose *stdout* of the mutation commands, which reaches toasts.

## Guard

`just verify` builds argv for all fifteen mutation variants and checks every
long flag it emits against the real `pueue <subcommand> --help`. A flag in the
wrong place, or one that doesn't exist, fails there instead of at runtime.
