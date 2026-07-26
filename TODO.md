# TODO

The single index of future work. `[S]`/`[M]`/`[L]` is effort; `P?` means the
shape isn't decided yet and there's a note in [`backlog/`](backlog/).

## Next

- `[S]` **Screenshots for the store** — 5 at 2000×1250, captured with Raycast's
  own *Save Screenshot* in dev mode so the chrome is right. The last unchecked
  store requirement.
- `[S]` **Interactive pass** — the paths that need a real keypress rather than a
  deeplink: kill/restart from the Tasks action panel, the log and follow views,
  the Add Task form, the Groups action panel, and clicking the menu bar item.
  See the checklist at the end of the build notes.
- `[S]` **`pueue send`** — a form to send input to a running task. Inherently
  best-effort: there is no way to know whether the process is reading stdin.
- `[S]` **Dependency graph in the task detail** — render `dependencies[]` as
  rows with their live status instead of a comma-separated list of ids.

## Later

- `[M]` **`pueue edit`** — round-trip pueue's TOML edit payload through a Form.
  Needs care: the task is `Locked` while editing, and abandoning the edit must
  restore it.
- `[M]` **Frecency-sorted command history** for Add Task, via
  `useFrecencySorting`.
- `[M]` **Remote profiles** — a `--profile` switcher with per-profile caching.
  Needs a real YAML dependency to read the `profiles:` block, which is why it
  isn't done already.
- `[S]` **Group-scoped clean** in the Groups view (`clean --group`).
- `[S]` **`pueue wait` notifier** — a `no-view` command that watches until a
  group drains and HUDs.

## Deferred, with notes

- `P?` `[L]` **Direct CBOR unix-socket transport** —
  [`backlog/socket-transport.md`](backlog/socket-transport.md)
- `P?` `[M]` **AI tools (`tools[]`)** — [`backlog/ai-tools.md`](backlog/ai-tools.md)
- `P?` `[S]` **Callback-driven notifications** —
  [`backlog/callback-notifications.md`](backlog/callback-notifications.md)
- `P?` **Publishing to the public store** —
  [`backlog/store-publishing.md`](backlog/store-publishing.md)

## Won't do

- **`pueue add --escape` as a UI option.** It escapes spaces along with every
  other metacharacter, and the command is passed as a single argv element, so it
  collapses the whole line into one token. Verified: it turns
  `echo not-a-pipe | wc -l` into `sh: echo not-a-pipe | wc -l: command not
  found`. The flag is only meaningful for the multi-word argv form this
  extension doesn't use.
- **Reading parallelism from `pueue parallel`.** Broken in 4.0.4 — with no
  argument it logs "Received unhandled response message", exits 0, and prints
  nothing. `group --json` is authoritative.
