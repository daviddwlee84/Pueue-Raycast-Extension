# TODO

The single index of future work. `[S]`/`[M]`/`[L]` is effort; `P?` means the
shape isn't decided yet and there's a note in [`backlog/`](backlog/).

## Next

- `[S]` **Screenshots for the store** — 5 at 2000×1250, captured with Raycast's
  **Window Capture** + "Save to Metadata". The last unchecked store requirement.
  See [`metadata/README.md`](metadata/README.md).
- `[S]` **Remaining interactive pass** — paths needing a real keypress rather
  than a deeplink, not yet exercised: submitting the **Add Task** form, the
  **Groups** action panel and its two confirmations, **Quick Add** from root
  search, and firing an action from the **menu bar**.

  Verified interactively already: `⌘⇧K` kill and `⌘⇧R` restart in the Tasks view
  (the optimistic update holds through the reconcile — no flicker), `⏎` log view,
  and `⌘L` live follow. The menu bar's rendering, title-at-zero, failure tint,
  and self-refresh on its interval are verified too.
- `[S]` **Verify remote against a real second machine** — the plumbing is
  asserted and was exercised against a second client config, but never against
  an actual remote `pueued` over a forwarded socket. See
  [`docs/remote.md`](docs/remote.md).
- `[S]` **`pueue send`** — a form to send input to a running task. Inherently
  best-effort: there is no way to know whether the process is reading stdin.
- `[S]` **Dependency graph in the task detail** — render `dependencies[]` as
  rows with their live status instead of a comma-separated list of ids.

## Docs site

Live at <https://daviddwlee84.github.io/Pueue-Raycast-Extension/>, bilingual
(en + zh-TW), deployed by `.github/workflows/docs.yml` on every push touching
`docs/`, `mkdocs.yml`, or `pyproject.toml`.

- `[S]` **Keep the zh-TW pages in step with the English ones.** They are
  suffix siblings (`remote.md` / `remote.zh-TW.md`), so an edit to one silently
  leaves the other stale — `fallback_to_default: true` means a missing page
  falls back rather than 404s, which hides drift.
- `[S]` **Consider surfacing `pitfalls/` and `backlog/` on the site.** They are
  repo-only today and linked by absolute GitHub URL, because MkDocs cannot
  resolve a relative `.md` link that leaves `docs/`.
- `[?]` **`--strict` is off in CI.** `mkdocs-llmstxt` and `mkdocs-static-i18n`
  are incompatible under it: llmstxt's source-path lookups break once i18n
  remaps the page index. We kept `/llms.txt`. Revisit if llmstxt gains i18n
  awareness.

## v0.3.0 candidates

Connection UX is done; these are the next layer.

- `[M]` **Group progress bars** — `pqsum` already shows `done/total`, a bar, and
  an ETA per group, and the same numbers would read well as a `List.Item`
  accessory or in the Groups detail. Everything needed is already in `State`:
  done vs total per group, and `durationMs` over finished tasks gives the
  average for an ETA. See `~/.local/share/chezmoi/docs/tools/pueue.md`.
- `[L]` **A fleet view — all connections at once** — the equivalent of
  `fleet pueue`: one row per (connection, group), read concurrently. Now cheap,
  because SSH mode multiplexes at 10–30 ms per call, so N hosts is N parallel
  reads rather than N handshakes. Needs a decision on failure isolation: one
  unreachable host must degrade its own row, never the view.
- `[S]` **Per-connection menu bar counts** — the menu bar follows one connection
  today. Showing `2 local · 5 lab` would need a read per connection on every
  interval, which is affordable now but should be opt-in.

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
