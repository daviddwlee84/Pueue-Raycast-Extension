# A launchd job extension, as the macOS answer to `systemd-run`

**Status:** `P?` · **Effort:** `[M]` for the useful subset · not started ·
**out of scope for this extension** — recorded because the question was asked

## What

[A note comparing `systemd-run --user`, `pueue`, `nohup`, and `tmux`](https://gist.github.com/daviddwlee84/35a2aa5d477c99c8615a6232f1a1f308)
lands on a division of labour: **`pueue` for many bounded shell jobs,
`systemd-run --user` for a few important long ones, systemd `.timer`/`.path` for
real schedules.** This extension covers the first column. The question is whether
the other two are worth a sibling extension on macOS, where the answer to systemd
is launchd.

## The mapping

| systemd | launchd | State |
| --- | --- | --- |
| `.service` unit file | `~/Library/LaunchAgents/*.plist` | file-based; `plutil -convert json` parses it cleanly |
| `.timer` | `StartInterval` / `StartCalendarInterval` | plist keys, not a separate object |
| `.path` | `WatchPaths` / `QueueDirectories` | plist keys |
| `systemd-run --user` (transient) | `launchctl submit` | still present on Darwin 25.3, but **deprecated, and it keeps the job alive on failure** — the wrong semantics for one-shot |
| `journalctl -u <unit>` | *nothing equivalent* | a job must set `StandardOutPath`; otherwise output scatters into `log show` |
| `systemctl show -p Result` | `launchctl print` | a bespoke indented format, not machine-readable |
| `loginctl enable-linger` | agents run at login; `LaunchDaemons` need root | different model, not a flag |

## The load-bearing feasibility finding

The pueue extension got `--json` for free. A launchd one would not. Checked here:

```console
$ plutil -convert json -o - ~/Library/LaunchAgents/homebrew.mxcl.pueue.plist
{"StandardErrorPath":"/usr/local/var/log/pueued.log", … ,"Label":"homebrew.mxcl.pueue"}

$ launchctl list com.apple.Finder | plutil -convert json -o - -
Property List error: Unexpected character { at line 1
```

`launchctl list <label>` emits an old-style dict that `plutil` **rejects**, and
`launchctl print gui/$UID/<label>` emits its own indented format. Only the bare
`launchctl list` three-column TSV (PID, LastExitStatus, Label) is trivially
parseable.

So the data model is forced: **parse the plists for definition, use the TSV for
live state, and own a parser for anything beyond that.** That is a real
maintenance surface — the kind of thing `pitfalls/pueue-status-enum-is-externally-tagged.md`
exists to record, except here you would be writing the parser rather than reading
a documented one.

## Where it sits against the store

[Launch Agents](https://www.raycast.com/stevensd2m/launch-agents) (1,214
installs) already does list / load / unload / remove. That is the *management*
half, and it is occupied.

The unoccupied ground is what the gist actually cares about: **one-shot
supervised jobs with retrievable output**, the `systemd-run --user` role. Doing
that properly means writing the plist yourself — `RunAtLoad`, a
`StandardOutPath` under a scratch directory, `launchctl bootstrap gui/$UID`, then
`bootout` on completion — because `launchctl submit` restarts on failure and
cannot be talked out of it.

## Verdict

**Lower value than this extension for comparable effort.** No `--json`, no queue
semantics, no parallelism control, no dependency wiring — and the surface that
most needs a GUI (transient one-shot jobs) is the one launchd supports worst.
Writing throwaway plists from an extension also means writing files into
`~/Library/LaunchAgents`, which is a much larger blast radius than shelling out
to a CLI that owns its own state.

The `[M]`-sized version that *is* worth doing is narrower and complementary
rather than competitive: **a menu bar plus list view over the user's own
LaunchAgents**, surfacing `LastExitStatus`, the next fire time computed from
`StartCalendarInterval`, and a tail of `StandardOutPath`. That is the
observability half — where a plist-only data model is sufficient, where the
existing extension is weakest, and where nothing has to be written to disk.

Everything this extension learned would transfer: the launchd `PATH` trap is
*more* acute there (see `pitfalls/raycast-launchd-path-pueue-not-found.md`), the
menu-bar constraints are identical, and the error-descriptor pattern in
`src/lib/error-states.tsx` maps directly onto "this agent is not loaded".

## What would have to be true

A real recurring need for launchd job observability on this machine — not a
hypothetical one — and the remaining `[S]` items here (screenshots, the
interactive pass, verification against a real second machine) done first.

## Related

- [`../docs/prior-art.md`](../docs/prior-art.md) — extensions that solve the same
  shape of problem, including the two named above
- [`../docs/raycast-surfaces.md`](../docs/raycast-surfaces.md) — why a menu bar
  command is the only ambient surface available
