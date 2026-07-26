# TODO

The single index of future work. `[S]`/`[M]`/`[L]` is effort; `P?` means the
shape isn't decided yet and there's a note in [`backlog/`](backlog/).

## Next

- `[S]` **Screenshots for the store** — the last unchecked store requirement, and
  the only one that cannot be automated: Window Capture needs a GUI hotkey and a
  ticked "Save to Metadata". Run `just shots`, which seeds the queue and walks
  the four view commands by deeplink so all that is left is the keypress; the
  menu bar shot is manual because a `menu-bar` command cannot be deeplinked.
  Then `just preflight` to confirm 3–6 files at exactly 2000×1250. See
  [`metadata/README.md`](metadata/README.md).
- `[S]` **De-stale the "not on the store yet" copy** after publishing — it is in
  both [`docs/getting-started.md`](docs/getting-started.md) and its zh-TW sibling,
  and `fallback_to_default: true` will not surface the drift.
- `[S]` **Remaining interactive pass** — paths needing a real keypress rather
  than a deeplink, not yet exercised: submitting the **Add Task** form, the
  **Groups** action panel and its confirmations (now including the four batch
  actions and the `Custom…` parallelism form), **Quick Add** from root search,
  and firing an action from the **menu bar**. Everything from v0.3.0 is in this
  bucket, including the connection-switch fix — see the checklist in the commit
  for `Stop an unreachable daemon from borrowing another one's queue`.

  Verified interactively already: `⌘⇧K` kill and `⌘⇧R` restart in the Tasks view
  (the optimistic update holds through the reconcile — no flicker), `⏎` log view,
  and `⌘L` live follow. The menu bar's rendering, title-at-zero, failure tint,
  and self-refresh on its interval are verified too.

  Verified against a live daemon rather than in the UI: both
  `restart --failed-in-group` forms, `clean --group`, and the fact that
  `--failed-in-group` on a group with no failures — or no such group — exits 0
  silently, which is why those actions are hidden rather than disabled.
- `[S]` **Verify remote against a real second machine** — the plumbing is
  asserted and was exercised against a second client config, but never against
  an actual remote `pueued` over a forwarded socket. See
  [`docs/remote.md`](docs/remote.md).
- `[S]` **`pueue send`** — a form to send input to a running task. Inherently
  best-effort: there is no way to know whether the process is reading stdin.

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

## Later

- `[M]` **`pueue edit`** — round-trip pueue's TOML edit payload through a Form.
  Needs care: the task is `Locked` while editing, and abandoning the edit must
  restore it.
- `[M]` **Frecency-sorted command history** for Add Task, via
  `useFrecencySorting`.
- `[M]` **Remote profiles** — a `--profile` switcher with per-profile caching.
  Needs a real YAML dependency to read the `profiles:` block, which is why it
  isn't done already.
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

- **A fleet view — every connection in one list.** The `fleet pueue` equivalent:
  one row per (connection, group), read concurrently. Technically cheap now that
  SSH multiplexing puts a remote read at 10–30 ms, and dropped anyway — it would
  make the extension meaningfully more complicated than the problem it solves.
  Switching connection is already a keystroke, and the opt-in per-connection
  counts on the menu bar's Connection rows answer "how are my machines" at a
  fraction of the cost. Revisit only if someone is actually running enough hosts
  to need it.
- **`pueue add --escape` as a UI option.** It escapes spaces along with every
  other metacharacter, and the command is passed as a single argv element, so it
  collapses the whole line into one token. Verified: it turns
  `echo not-a-pipe | wc -l` into `sh: echo not-a-pipe | wc -l: command not
  found`. The flag is only meaningful for the multi-word argv form this
  extension doesn't use.
- **Reading parallelism from `pueue parallel`.** Broken in 4.0.4 — with no
  argument it logs "Received unhandled response message", exits 0, and prints
  nothing. `group --json` is authoritative.
