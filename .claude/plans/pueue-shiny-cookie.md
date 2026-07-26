# Agent Deck — a Raycast quick menu for AI coding agents

> New repo at `../Agent-Deck-Raycast-Extension`, sibling to this one. Nothing in this repo changes.

## Context

You run ~50 concurrent coding agents across Claude Code, Codex, OpenCode and Gemini, inside herdr
workspaces. Two things cost you time daily:

1. **"I've solved this before, but where?"** — you remember running a similar session but not which
   agent, which directory, or which session id, so you can't resume it.
2. **"Which agent needs me right now?"** — 50 idle, 2 blocked, and no single surface that says which.

CodeIsland answers #2 for the *terminal app* but not the *pane*, and it is a Swift notch app.
CodexBar answers quota but nothing else. `tv agent-sessions` / `tv agent-panes` in your chezmoi repo
already answer both — but only from inside a terminal, and only via a 1325-line Python helper that
can't be reached from anywhere else on the machine.

**The thesis, and it is measurable:** all 52 live herdr agents have a session UUID that resolves in
both the SpecStory index *and* `~/.claude/projects/`. Search results and live processes are the same
objects. No existing Raycast extension joins them — ccusage is cost-only; Threadlens, claude-history
and raycast-claude-sessions are search-only; ClaudeCast is remote-control-only; none has a menu bar.

**Outcome:** a Raycast menu bar showing blocked-first agent state, a full-text session search that
tells you where and how to resume, and one keystroke from either into the right terminal pane.

---

## Verified facts (measured on this machine, 2026-07-27)

Everything below was run, not assumed. These numbers belong in code comments.

| Fact | Measurement | Consequence |
|---|---|---|
| `herdr agent list` | **52 ms**, 52 agents, 50 idle / 2 blocked, JSON native (no `--json`) | menu-bar-cheap at 1 min |
| SpecStory FTS rank query | **282 ms** incl. sqlite3 spawn (2 tokens, 90 fanout, joined) | typeahead-viable |
| `snippet()` on the largest matching body (7.36 MB) | **1144 ms** vs **20 ms** rank-only — **57×** | snippet can never be on the typeahead path |
| Body sizes in `sessions_fts` | avg **257 KB**, max **9.99 MB** | snippet cost is superlinear in body size |
| Duplicate `(agent, session_id)` FTS rows | **81 groups >1, max 6** (46×2, 23×3, 6×4, 5×5, 1×6) | naive join fans out 6× |
| Empty `updated_at` | **7 rows, all `project_id='unknown'`** — `created_at`/`origin_cwd`/`slug` also empty | `new Date("")` → Invalid Date; must yield `undefined` |
| One repo, two `project_id`s | this repo: `b1f0-…` (workspace) + `f1dd-…` (git), both in `.specstory/.project.json` | never group by `project_id`; group by `origin_cwd` |
| `~/.claude/sessions/*.json` | 49 files, **49 alive, 48 distinct sessionIds** — pids 21952 + 95663 share `1b31cf2b-…` | `sessionId` is a 1:N link, **not** a key |
| `herdr agent get/focus <target>` | `pane_id` ✅; `terminal_id` ✗; session UUID ✗ → `{"error":{"code":"agent_not_found"}}` | `pane_id` is the only actuator handle |
| herdr with no socket | prints `Error: Os { code: 2, kind: NotFound … }` — **not JSON** | classify on "stdout didn't parse", never on exit code |
| `sqlite3` | **only** `/usr/bin/sqlite3` (3.51, FTS5, `-json`). No Homebrew build | probe must reach `/usr/bin` |
| `node:sqlite` | Node 22 → behind `--experimental-sqlite`; Raycast controls argv | shell out to `/usr/bin/sqlite3` |
| SpecStory provider coverage | claude 451, codex 18, gemini 1. Supports antigravity/claude/codex/cursor/cursoride/deepseek/droid/gemini — **no opencode** | OpenCode needs its own index reader |
| `opencode.db` | 93 sessions, rich metadata (`title`,`directory`,`slug`,`agent`,`model`,`cost`,`tokens_*`) but **no FTS table** | metadata search cheap; full-text = LIKE scan over `part.data` |
| Codex on disk | 19 `rollout-*.jsonl`; **no `session_index.jsonl`** (the tv channel assumes one); `~/.codex/history.jsonl` 84 KB | SpecStory already covers codex; fs is the fallback |
| Cursor | **0** `~/.cursor/chats/**/store.db` | cursor support is theoretical here — ship it dark |
| Claude transcript record types | assistant, user, ai-title, last-prompt, mode, permission-mode, attachment, agent-name, file-history-delta, system, file-history-snapshot, queue-operation. **No `type:"summary"`** on CLI 2.1.220 | title = **last** `ai-title`, not `summary` |
| `~/.claude/projects/` dir names | `/`, `.`, `_` all → `-`. `-Users-david--local-share-chezmoi` was `/Users/david/.local/share/chezmoi` | mangling is **irreversible**; read `cwd` from inside the file |
| The proxy | `ANTHROPIC_BASE_URL=http://localhost:4142`, `ANTHROPIC_AUTH_TOKEN=dummy`, bun pid 25747. **`"model":"gemini-2.5-pro"` appears in Claude Code's own transcripts** (3 records / 7 d, alongside opus-4-8 ×2248, opus-5 ×733) | an untagged "Anthropic quota %" would be a confidently-wrong number |
| Claude credentials | `~/.claude/.credentials.json` **absent**; Keychain `Claude Code-credentials` present | OAuth path needs an ACL prompt from a launchd process → rejected |
| `codexbar` | documented in your chezmoi `docs/tools/codexbar.md`, installed by the `coding_agents` ansible role — **not installed on this Mac** | absent is the *normal* case, must be first-class |
| herdr host app | Alacritty (pid 516) | `herdr agent focus` moves focus *inside* herdr; host still needs `open -a` |

**Correction to note:** `bm25()` inside a CTE does work here (I tested it). Still prefer the magic
`rank` column — it's what the FTS5 docs guarantee in an `ORDER BY` — but don't write a pitfall
claiming bm25 is categorically broken.

---

## The two constraints you set

**1. Multi-agent, not Claude-only.** Claude / Codex / OpenCode / Gemini are all first-class; Cursor
ships dark. This is why `SessionIndex` is a *provider interface with three implementations* rather
than "the SpecStory reader".

**2. Every external tool is optional.** herdr, specstory, sqlite3, codexbar, peon, pueue — none is
required. Missing tool → fall back if a fallback exists, otherwise render the feature **disabled with
an install hint**, never an error and never a crash. This gets its own module (`lib/agents/capabilities.ts`)
rather than being scattered `try/catch` — see below.

---

## Capability model (the load-bearing idea)

One pure module decides what the whole app can do. Probed once per command launch, cached in-memory.

```ts
// lib/agents/capabilities.ts — PURE. Takes an injected `exists` + `probeVersion`.
type Capability =
  | "live.herdr"        // herdr binary + server answering
  | "live.claude"       // ~/.claude/sessions/ readable   (no binary needed — always on)
  | "index.specstory"   // sqlite3 + ~/.specstory/sessions.db + schema matches
  | "index.opencode"    // sqlite3 + ~/.local/share/opencode/opencode.db
  | "index.claudefs"    // ~/.claude/projects/            (no binary needed — always on)
  | "index.codexfs"     // ~/.codex/sessions/             (no binary needed — always on)
  | "act.herdr" | "quota.codexbar" | "sound.peon" | "queue.pueue";

interface CapabilityState {
  available: boolean;
  reason?: string;                       // "herdr not found on PATH or in probe dirs"
  install?: { label: string; command: string; url?: string };  // rendered as a copyable hint
  degradedTo?: Capability;               // what took over
}
```

Rules, enforced in `dev-check.ts`:
- **No capability is required.** With every optional tool absent the app still runs on
  `live.claude` + `index.claudefs` + `index.codexfs` alone — all three are pure filesystem reads.
- A missing capability produces a **disabled UI affordance with an install hint**, not an
  `ErrorDescriptor`. `ErrorDescriptor` is reserved for a tool that is *present but broken*
  (herdr installed, server down) — that distinction is the whole point.
- The capability set is rendered once, honestly, in a **"Sources" section** at the bottom of the
  menu bar and as a `List.EmptyView` action in both views.

Install hints to pre-write: `brew install --cask codexbar`, `brew install specstoryai/tap/specstory`,
herdr → its GitHub releases URL, `brew install pueue`, peon → its tap.

---

## Domain types

```ts
type AgentKind = "claude" | "codex" | "opencode" | "gemini" | "cursor" | "unknown";
type AgentStatus = "idle" | "working" | "blocked" | "done" | "unknown";  // herdr's enum, normalized

/** A conversation. Matches SpecStory's own PK. */
type SessionKey = { agent: AgentKind; sessionId: string };

/** A *process*. NOT keyed by sessionId — verified: pids 21952 and 95663 are both
 *  alive and both report sessionId 1b31cf2b-…, one idle one busy. */
interface LiveAgent {
  handle: string;              // "herdr:w3:p7" | "claude-pid:18933"
  source: "herdr" | "claude-registry";
  agent: AgentKind;
  status: AgentStatus;
  cwd: string;
  session?: SessionKey;        // the join. 1:N from SessionKey → LiveAgent
  title?: string;
  paneId?: string;             // herdr only — the ONLY thing `herdr agent focus` accepts
  pid?: number;                // registry only
  detectedBy?: string;         // herdr's rule name, so "detected" never reads as "known"
}

interface AgentSession {
  key: SessionKey; title: string; cwd?: string; project?: string;
  createdAt?: Date; updatedAt?: Date;          // undefined, never 1970
  userTurns?: number; totalTurns?: number;
  transcriptPath?: string; markdownPath?: string;
  origin: Capability;                          // which index produced this, for the Sources UI
}

interface SessionHit {
  session: AgentSession; score: number;
  matched: ("fts" | "prompt" | "title" | "cwd" | "live")[];
  snippet?: string; live: LiveAgent[];
}
```

---

## Commands (package.json)

**v0.1**

| name | mode | notes |
|---|---|---|
| `agent-menu` | menu-bar | `interval: "1m"`. Icon tinted red when `blocked > 0` |
| `sessions` | view | arg `query` (optional) seeds `searchText` |
| `agents` | view | live list + detail pane |
| `focus-agent` | no-view | arg `target` (required) — root-search jump |

`agent-menu` sections: **Blocked** → **Working** → **Idle** (default hidden; 50 items is the real
scaling problem) → `Updated HH:MM` disabled row → **Sources** (capability state) → launchers.
`updateCommandMetadata({ subtitle: "2 blocked · 0 working · 50 idle" })` in `onData`.

Per-command preferences: `titleCounts` (`blocked` default / `blocked-working` / `working` / `none`),
`maxItemsPerSection` (7), `showIdle` (off), `groupIdleByProject` (on), `maxResults` (30),
`showSnippets` (**off**, description carries the 20 ms → 1144 ms measurement),
`agentFilter` (`;`-separated — Raycast preferences cannot be multiline, see this repo's pitfall).

Extension preferences: `herdrPath`, `sqlitePath`, `specstoryDb`, `opencodeDb`, `claudeHome`
(honours `CLAUDE_CONFIG_DIR`), `codexHome`, `terminalApp` (appPicker, raised via `open -a` after focus),
`liveSource` (`auto` / `herdr` / `claude-registry` / `both`), `confirmDestructive`.

**Deferred:** v0.2 `agent-sounds` (peon) + mutations + pueue wakeup + ⭐ review inbox.
v0.3 `usage`. v0.4 herdr socket events.

---

## `src/` layout

Mirrors this repo's layering. **One seam per capability, not one per tool** — six tools but only
three capabilities and one action channel, so `herdr` implements `LiveAgentSource` + `AgentActuator`,
`specstory`/`opencode`/`claude-fs` each implement `SessionIndex`, `codexbar` implements `QuotaSource`.
That preserves the Mutation-as-data discipline without minting six interfaces with one impl each.

```
src/
  agent-menu.tsx  sessions.tsx  agents.tsx  focus-agent.tsx

  lib/
    agents/                    THE DOMAIN — no @raycast/api below this line
      types.ts                 the types above
      transport.ts             TYPES ONLY: SessionIndex, LiveAgentSource, AgentActuator,
                               QuotaSource + the AgentAction data union
      capabilities.ts          PURE probe/degrade logic (see above)
      probe.ts                 PURE: probeFor(name, dirs, exists), PROBE_DIRS, pickBinary()
      normalize.ts             normalizeStatus(), keyOf(), titleOf(), cwdOf(), parseTs()
      merge.ts                 mergeLiveAgents(), attachLive(), dedupeFtsRows(), mergeIndexes()
      rank.ts                  PURE ranker — scoreHit(), blend(), tieBreak()
      errors.ts                AgentDeckError { kind, source, detail } + classify()
      index.ts                 factory + facade; setSources() test hook

    herdr/    binary.ts  wire.ts  errors.ts  cli.ts      (cli.ts is the only spawner)
    specstory/binary.ts  fts.ts   sql.ts  wire.ts  cli.ts
    opencode/ sql.ts     wire.ts  cli.ts               [SessionIndex #2 — metadata + LIKE]
    claude/   paths.ts   registry.ts  history.ts  transcript.ts
    codex/    paths.ts   rollout.ts   history.ts       [SessionIndex #4 — fs fallback]

    actions.tsx  optimistic.ts  error-states.tsx  format.tsx
    session-detail.tsx  agent-tail.tsx
    dev-check.ts  fixtures/*.json
```

### One deliberate improvement over this repo

`src/lib/dev-check.ts:` re-declares `PROBE_DIRS` because `binary.ts` imports `@raycast/api`. That
duplication will drift the moment a new dir is added — and one already must be: **`~/.opencode/bin`**
is in none of the six standard probe dirs. Split it instead: `lib/agents/probe.ts` is pure and holds
the list; `lib/<tool>/binary.ts` is Raycast-aware and calls it with `getPreferenceValues()` +
`existsSync`. dev-check then asserts the *real* order with an injected fake `exists`, including
pref-before-cache and stale-pref-falls-through.

**Import discipline enforced twice:** an eslint `no-restricted-imports` override scoped to the pure
globs (fast feedback) **and** a dev-check assertion that reads each file and fails on an
`@raycast/api` import (survives an inline eslint-disable).

---

## Search design

```
keystroke → debounce 150 ms, abortable
  ├─ empty  → recent() across all available indexes            ~15 ms
  └─ text   → parallel, each gated on its capability:
       A. specstory FTS rank query      282 ms   (spawn + query)
       B. opencode metadata LIKE        ~20 ms   (93 rows, no FTS)
       C. ~/.claude/history.jsonl       ~2 ms    (in-memory, mtime-cached)
       D. ~/.codex/history.jsonl        ~1 ms    (in-memory, mtime-cached)
       E. live agents                   0 ms     (already in the menu-bar cache)
     → merge → dedupe → rank → top 30

selection → debounce 250 ms, 2 s cap, only if showSnippets
       F. snippetQuery(rowid)           0.2–1.2 s
```

**Rank query** — `LIMIT` inside the CTE is load-bearing twice (keeps `rank` available, bounds the join):

```sql
WITH hits AS (
  SELECT rowid AS rid, agent, session_id AS sid, rank AS r
  FROM sessions_fts WHERE sessions_fts MATCH :expr
  ORDER BY rank LIMIT :fanout            -- 3 × maxResults, to survive dedupe
)
SELECT h.agent, h.sid, h.r, h.rid, s.project_name, s.origin_cwd, s.slug, s.name,
       s.native_path, s.user_turns, s.total_turns,
       NULLIF(s.created_at,'')                                     AS created_at,
       NULLIF(COALESCE(NULLIF(s.updated_at,''), s.created_at), '') AS ts
FROM hits h JOIN sessions s
  ON s.agent = h.agent AND s.session_id = h.sid AND s.deleted = 0
ORDER BY h.r;
```

**Snippet** — by `rowid` only. `rowid` is fts5's real PK; `session_id` is `UNINDEXED`, so the
`session_id IN (…)` form is a scan, not a lookup.

**FTS5 sanitizer** (`specstory/fts.ts`) — a raw user string is *not* an expression. Verified errors:
`-x` → `no such column: x`; `col:x` → `no such column: col`; `foo"` → `unterminated string`;
`a AND` → `fts5: syntax error near ""`.

```
tokens = text.split(/\s+/).filter(Boolean)
each → '"' + tok.replace(/"/g,'""') + '"'
last token, if no trailing space → append '*'
join ' '        // fts5 implicit AND
```
`raycast extension` → `"raycast" "extension"*`. A leading `/` = raw FTS5 escape hatch; a syntax
error renders as a **non-structural** EmptyView with the grammar, mirroring this repo's `isBadQuery`.

**Ranking** (`rank.ts`, pure; weights in one exported object so dev-check asserts *ordering
invariants*, not float equality):

```
0.55 ftsNorm + 0.20 recency(exp(-ageDays/14)) + 0.15 live + 0.10 promptHit + 0.05 titleHit
tie-break: updatedAt ↓ → createdAt ↓ → sessionId ↑     (total order, always)
```

**The four gotchas, handled:**
- **6-way duplicates** → reduce to best (most negative) `r`, keep its `rid` for the snippet.
- **Empty `updated_at`** → double `NULLIF` yields SQL `NULL` → `undefined`. Renders `date unknown`,
  sorts last in band. **Never** `new Date("")` and **never** 1970.
- **Two `project_id`s per repo** → group by `origin_cwd` (trailing-slash normalized, `/private/tmp`→`/tmp`).
- **70 % of rows have empty `name`** → title chain: `name` → last `ai-title` from transcript → `slug`
  → first `display` in history.jsonl → `sessionId.slice(0,8)`. Only the middle two do I/O; both lazy + cached.

**Caching** — parsed `history.jsonl` at module level keyed on `mtimeMs`+size; live `Snapshot` in
`useCachedPromise`; resolved titles in `Cache` keyed `title:<agent>:<sid>`. **Never cache transcript
bodies or snippets** — `useCachedPromise` writes to a plaintext disk `Cache`, and a transcript is
strictly worse than pueue's `envs` (it contains whatever you pasted, including `pastedContents`).

**Cold start**: the first FTS read after login faults in a 290 MB b-tree. Fire a throwaway
`SELECT count(*) FROM sessions` at view mount and render `recent()` meanwhile.

---

## Quota (v0.3, per your choice)

Primary **`codexbar usage --provider <p> --source cli --json`**; fallback an auth-free local
token/cost rollup from `~/.claude/projects/**/*.jsonl` + `~/.codex/sessions/**/*.jsonl` +
`opencode.db`'s built-in `cost`/`tokens_*` columns. codexbar absent is the *normal* case → capability
hint with a copyable `brew install --cask codexbar`, not an error.

`quota/confidence.ts` (pure) reads the effective env: `ANTHROPIC_BASE_URL` not api.anthropic.com, or
`ANTHROPIC_AUTH_TOKEN` set → every bucket downgraded to `proxied`, rendered greyed with the accessory
`via localhost:4142`. **Command is named "Agent Usage", not "Quota"** — it leads with tokens/cost per
model, which is always true, and treats provider quota as a secondary confidence-tagged section.

**Rejected outright** (→ `TODO.md` "Won't do", all three reasons recorded): the OAuth
`/api/oauth/usage` endpoint. `~/.claude/.credentials.json` doesn't exist so the token is in the
Keychain, and `security find-generic-password -w` triggers an ACL prompt attributed to Raycast from a
launchd process — remembered if denied. Plus community-reported per-token 429s. Plus it measures the
wrong account here.

**Backlogged with "what would have to be true"**: the statusline shim. Teeing `rate_limits` from
stdin would mean editing `dot_claude/modify_settings.json.tmpl` in your chezmoi repo — a cross-repo
change to a `modify_` template, sitting on the hot path of every statusline render in all ~49 live
sessions, for a decorative feature. And it wouldn't help while the proxy is in place.

---

## Phasing

- **v0.1** — search, live, menu bar, focus. **Read-and-navigate only**, so `optimistic.ts` /
  `actions.tsx` are stubs and herdr's unmeasured reconcile latency blocks nothing. Full capability
  model + `error-states.tsx` for: herdr present-but-server-down, sqlite3 absent, SpecStory DB absent,
  schema mismatch, FTS syntax error. `dev-check.ts` + fixtures + `just check`. Docs en + zh-TW,
  `pitfalls/`, `backlog/`, `TODO.md`.
- **v0.2** — mutations behind `act()` (**gated on measuring herdr's reconcile latency**, below),
  resume-into-new-herdr-tab, peon, pueue `agent-wakeup` (note: the group **does not exist** — the
  action must create it), ⭐ review inbox (0 panes carry `tokens.review` today → ships dark).
- **v0.3** — Agent Usage. **v0.4** — herdr socket events (protocol 17 exposes `subscription_event`),
  opencode full-text, cursor.

---

## dev-check fixtures

Same harness shape as this repo: plain script, `tsc` + `node`, JSON fixtures not mocks, live
cross-checks that skip when a binary is absent.

Capture (**redaction mandatory** — `ray publish` copies the directory, not the git index; a
`.gitignore` will not save you. `just fixtures` captures *and* redacts in one step: real paths →
`/Users/dev/Projects/<name>`, UUIDs → deterministic fakes **stable across fixtures so the joins still
assert**, keep one CJK and one emoji title):

- `herdr-agent-list.json` — all 52; the 2 blocked; the 6 with no `terminal_title`; the 8 sharing
  `cwd:/Users/david`; one CJK title; one hand-added `agent_session.kind:"path"` row
- `herdr-errors.json` — `{"error":{"code":"agent_not_found",…}}`; the non-JSON `Error: Os {…}`; empty stdout exit 0
- `herdr-api-schema.json` — `$defs.AgentStatus.enum`, drives the live cross-check
- `specstory-rows.json` — the 6-way duplicate; a `project_id='unknown'` row; both project_ids for one
  `origin_cwd`; empty `name`; a codex row; the single gemini row
- `opencode-sessions.json` — rows with `cost`/`tokens_*` populated and a null `agent`
- `fts-queries.json` — input → expected expression **plus the verbatim sqlite error** each
  unsanitized form produces
- `claude-sessions.json` — the duplicate-sessionId pair (21952 idle / 95663 busy)
- `claude-transcript.jsonl` — all 12 record types, **three** `ai-title` lines with different values
  (assert **last** wins), **zero** `type:"summary"`
- `claude-history.jsonl`, `codex-history.jsonl`, `peon-status.txt`
- `codexbar-usage.json` — **not capturable**; transcribe from your `docs/tools/codexbar.md` and mark
  `"_unverified": "shape from chezmoi docs; codexbar not installed on the capture machine"`

Key assertions: both status vocabularies → the 5-value union, `unknown` never crashes; 6 FTS rows →
1 hit; empty `updated_at` → `undefined` (assert **both** negatives: not Invalid Date, not 1970); the
duplicate-sessionId fixture → one `AgentSession` with `live.length === 2`; ranker total-ordering and
never-throws-on-undefined; sanitizer idempotence and no unbalanced quotes; probe order reaches
`~/.opencode/bin`; **every capability absent → the app still returns results**; import discipline.

Live cross-checks (stronger than this repo's `--help` check, because herdr publishes a schema):
`herdr api schema --json` `$defs.AgentStatus.enum` **deep-equals** our union (catches a herdr upgrade
at `just verify` time); `PRAGMA compile_options` contains `ENABLE_FTS5`; every generated FTS
expression parses against the real DB (as `count(*)`, never with `snippet()`).

---

## pitfalls/ to pre-write

Symptom-titled, each opening with a grep-able verbatim block:

1. `fts5-snippet-costs-seconds-not-milliseconds.md` — 20 ms → **1144 ms** on a 7.36 MB body
2. `one-session-appears-six-times-in-the-search-results.md` — 81 groups >1, max 6
3. `a-session-with-no-date-sorts-to-1970.md` — 7 rows, all `project_id='unknown'`
4. `the-claude-projects-directory-name-cannot-be-turned-back-into-a-path.md`
5. `there-is-no-summary-record-in-a-claude-transcript.md` — it's `ai-title`, take the **last**
6. `herdr-agent-focus-only-accepts-a-pane-id.md` — verbatim `agent_not_found` for terminal_id + UUID
7. `a-dead-herdr-server-looks-like-zero-agents.md` — non-JSON stderr, exit code unreliable
8. `two-live-processes-report-the-same-session-id.md`
9. `an-anthropic-base-url-proxy-makes-the-quota-meter-lie.md` — gemini-2.5-pro in Claude transcripts
10. `the-same-repo-has-two-specstory-project-ids.md`
11. `a-user-typed-search-string-is-not-an-fts5-expression.md` — four verbatim errors
12. `node-sqlite-is-behind-a-flag-raycast-cannot-pass.md` — and there's no Homebrew sqlite3 here
13. `opencode-is-not-in-the-usual-probe-directories.md` — `~/.opencode/bin`
14. `specstory-does-not-index-opencode.md` — 8 supported providers, opencode isn't one

`backlog/` (what / why-not / what-would-have-to-be-true): `herdr-event-subscription.md`,
`local-usage-index.md`, `statusline-shim.md`, `oauth-usage-endpoint.md`, `cursor-session-index.md`,
`store-publishing.md`.

---

## Verification

`just check = typecheck verify lint build`, plus `dist` / `preflight` / `store-export` / `shots` /
`fixtures`; CI calls `just`. Structure copied from this repo's `Justfile`.

**End-to-end — from Raycast root search, never from the `ray develop` console.** The console inherits
your interactive PATH; Raycast's launchd process does not.

1. **Search** `raycast` → ≥5 rows in <400 ms. Type `-x` → grammar EmptyView, not a toast. Find a
   metadata-less row → `date unknown`, never 1970. A session with a live agent → `LIVE` tag and a
   working ⌘⏎ **Focus Running Agent**. `showSnippets` on, arrow through 10 rows fast → no beachball,
   at most one "preview unavailable" on the ~10 MB session. Reboot → first render is `recent()`.
2. **Multi-agent** — assert results include codex (18) and opencode (93) rows, not just claude.
   Rename `opencode.db` → opencode rows vanish, everything else still works.
3. **Every dependency optional** — the acceptance test for your second constraint. One at a time:
   move `herdr` aside → falls back to `~/.claude/sessions` (~49 agents) with a "herdr not installed"
   Sources hint; move `sqlite3` aside → both DB indexes disable, `claude-fs`+`codex-fs` still return
   results; rename `sessions.db` → SpecStory disables. Then **all of them at once** → the app must
   still list live Claude agents and search the filesystem. No crash, no error toast, only hints.
4. **Present-but-broken ≠ absent** — stop the herdr server with `herdr` still installed → the
   **structural** "herdr server not running" screen with a copy action, *not* the install hint and
   *not* an empty list.
5. **Live** — menu title `2`, icon red; click a blocked agent → Alacritty raises **and** herdr lands
   on the right pane. Kill a `claude` process without removing its json → `ps` prunes it. Point
   `herdrPath` at a nonexistent file → probe takes over, not `spawn herdr ENOENT`.
6. **Blocks v0.2** — measure herdr's reconcile latency: `herdr agent prompt <pane> "hi"`, poll
   `herdr agent list` until `working`; five trials, min/median/max; repeat for `working → idle`.
   Those become `RECONCILE_DELAY_MS` / `RECONCILE_SETTLE_MS` with the measurement in the comment.
   **Do not copy 400/1500** — that's pueued's update loop; herdr screen-scrapes rendered terminal
   text (`rule: live_prompt_box, evidence: "❯\n"`) and will be slower and noisier.
7. **Store** — `just preflight`, `just dist`, `just store-export`. Then grep the committed fixtures
   for `/Users/david`, real repo names and real session UUIDs.

---

## First steps

1. `mkdir ../Agent-Deck-Raycast-Extension && git init`; vendor `raycast-extension-dev` +
   `project-knowledge-harness` + `agent-history-hygiene` from `.agents/skills/` with `skills-lock.json`
   and the `.claude/skills` symlinks; run its `scripts/new-raycast-extension.sh`.
2. Copy `Justfile`, `tsconfig.json`, `eslint.config.mjs`, `.github/workflows/` from this repo and adapt.
3. `just fixtures` → capture + redact, before any UI code exists.
4. `lib/agents/{types,transport,capabilities,probe,normalize,rank}.ts` + `dev-check.ts` — pure only,
   green under `just verify`, before touching React.
5. `lib/specstory/` then `lib/herdr/` then `sessions.tsx` — the thinnest vertical slice that proves
   the join.
