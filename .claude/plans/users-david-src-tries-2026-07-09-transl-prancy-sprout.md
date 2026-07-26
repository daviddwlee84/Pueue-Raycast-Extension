# Pueue Raycast Extension

## Context

You asked whether a Raycast "widget" for [Pueue](https://github.com/Nukesor/pueue) is feasible. Two premises needed correcting before designing anything:

1. **Raycast has no widget API.** The complete list of extension entry points in 2026 is `mode: "view"`, `mode: "no-view"`, `mode: "menu-bar"` (macOS-only), AI `tools[]` (Raycast **Pro**-gated), and Script Commands. Nothing widget-shaped shipped in "The New Raycast" (May 2026) or Glaze (July 2026); the "Widgets" pages on raycast.com are iOS-only. **The real target is a `mode: "menu-bar"` command** (`MenuBarExtra` + a manifest `interval`).
2. **Nobody has built this.** Verified four ways — `gh search code repo:raycast/extensions`, repo search, Raycast store search, web search. `jakevossen5/pueue-bar` (2021) is an empty repo with a one-line README. There is also no xbar/SwiftBar plugin and no Alfred workflow. This would be the first.

**Outcome:** a standalone, store-ready Raycast extension at `/Users/david/Documents/Program/Pueue-Raycast-Extension` (currently an empty git repo, zero commits) that shells out to the `pueue` CLI. A menu-bar command gives the glanceable surface; view commands (Tasks, Add Task, Groups, Quick Add) are where work happens.

**Decisions taken:** menu bar + full command set · shell out to `pueue --json` (CBOR socket transport is backlog, behind one file boundary) · build store-ready but defer `publish` · document the `daemon.callback` notification recipe, **never write to the user's `pueue.yml`**.

The closest structural precedent is Homebrew's `services-menu` in `raycast/extensions` (menu-bar, `interval: 1m`, count in `title`, optimistic refresh after each action). Its own header comment states our exact constraint: no push notification of state changes, so poll.

## Constraints verified on this machine

Pueue **v4.0.4**, Homebrew at `/usr/local/bin/pueue` (Intel prefix — `/opt/homebrew/bin` does not exist here), daemon under `brew services`/launchd, config at `~/Library/Application Support/pueue/pueue.yml` (*not* `~/.config/pueue/`). `pueue status --json` measures **22–44 ms** (median 28 ms). Raycast runs extensions on its own bundled **Node 22**.

These traps are load-bearing — each one is a bug if missed:

| # | Trap | Consequence |
|---|---|---|
| 1 | `--color` is a **global** flag. `pueue status --color never` → **exit 2**, clap error. Correct: `pueue --color never status --json`. | Exit codes are **0/1/2**, not 0/1. |
| 2 | `color_eyre` writes **ANSI to stderr even when piped**; neither `--color never` nor `NO_COLOR=1` suppresses it. | Any surfaced stderr must be ANSI-stripped, plus `Location:`/backtrace lines dropped. |
| 3 | `status` is a **nested externally-tagged enum**. There is no flat `"status":"Running"`. `Locked` is **recursive**. `TaskResult` mixes bare strings (`"Success"`) with objects (`{"Failed":127}`). | Every v3-era example online is wrong. |
| 4 | `Stashed` carries `enqueue_at`; every other variant carries `enqueue**d**_at`. | One-letter typo → silently undefined. |
| 5 | `new Date(null)` returns **1970-01-01**, not `Invalid Date`, and `Stashed.enqueue_at` is nullable. | Renders "1970" instead of "—" without a guard. |
| 6 | `group --json` returns the **inner map** (`{"default":{…}}`), a different top level from `status --json` (`{"tasks":…,"groups":…}`). | Sharing one parser is the easiest bug in the codebase. |
| 7 | `envs` is a **full snapshot of the submitting shell's environment**, present in `status --json` (~2 MB for 400 tasks upstream) and may contain secrets. | Must be stripped at the parse boundary before anything caches it to disk. |
| 8 | The daemon acks a request **before** its update loop applies it. | An immediate revalidate reads pre-change state and visually undoes the optimistic update. |
| 9 | `pueue kill -g GROUP` **also pauses the group**; `-a` pauses all. `pueue group remove NAME` **moves its tasks to `default`**. | Confirmation copy must say so. |
| 10 | `pueue parallel` with no args is broken (logs "Received unhandled response message", exits 0, prints nothing). | Read parallelism from `group --json`. |
| 11 | `pueue log <bad-id> --json` → `{}` with **exit 0**. | Empty ≠ error. |
| 12 | `interval` is a **manifest field**; a preference cannot change it. Brew hardcodes `"interval": "1m"` with no interval preference (verified in its installed manifest). Raycast renders its own refresh control in command settings. | Do not ship an interval preference. |
| 13 | Background refresh is **disabled by default for store installs** until the command is run once. | A fresh install shows nothing in the menu bar. Top README item. |
| 14 | On Raycast restart the menu-bar item is **restored from Raycast's database**, not by re-running the command. | Stale renders survive restarts (upstream #14250, #20031, #14659). |
| 15 | Raycast runs under launchd with no shell rc, so bare `pueue` throws `ENOENT` — **but a terminal hides this**, because `npm run dev`'s console inherits your full PATH. | Every PATH-related test must be run *from Raycast*. |

Only three commands emit JSON: `status`, `log`, `group`. All 15 mutations are exit-code + prose only. `status` accepts a client-side query DSL that composes with `--json` (`status=failed order_by id desc first 20`); note `columns=id,status` — the brackets in the grammar docs are meta-notation, `columns=[id]` fails.

## Repo layout — extension at the root

Not a subdirectory: `ray build`/`develop`/`lint` all resolve `./package.json` and write `./raycast-env.d.ts` relative to cwd, and `npm run publish` copies the extension directory verbatim into `extensions/pueue/`. The reference repo uses `raycast/extension/` only because its primary artifact is a Go CLI; here the extension *is* the repo.

```
package.json · package-lock.json (committed) · tsconfig.json · eslint.config.js
raycast-env.d.ts (generated, committed) · README.md · CHANGELOG.md · LICENSE · Justfile · TODO.md
assets/     extension-icon.png (512×512) + .svg source + pueue-menubar.svg (monochrome template glyph)
metadata/   pueue-1..5.png @ 2000×1250
docs/       pueue-json-contract.md · raycast-surfaces.md
backlog/    socket-transport.md · ai-tools.md · callback-notifications.md
pitfalls/   one file per trap above, titled by *symptom*, with a grep-able verbatim-error section
src/
  tasks.tsx · add-task.tsx · groups.tsx · quick-add.tsx · queue-menu.tsx
  lib/
    pueue/                    ← THE TRANSPORT SEAM
      index.ts                public API — the only thing views import
      types.ts                wire types, no I/O
      normalize.ts            pure enum flatteners (unit-testable)
      errors.ts               PueueError taxonomy + stderr normalizer
      binary.ts               resolvePueue / resolvePueued / baseEnv / log paths
      transport.ts            interface PueueTransport + Mutation union + factory
      cli-transport.ts        execFile/spawn impl  ← the ONLY file a socket transport replaces
    optimistic.ts             pure State → State updater per Mutation
    error-states.tsx          describeError() + <ErrorEmptyView/> + <ErrorDetail/>
    task-item.tsx · task-log.tsx · task-follow.tsx · group-dropdown.tsx · format.tsx · hooks.ts
```

Adopts the reference repo's knowledge-harness convention (`TODO.md` as a priority/effort index, `backlog/<slug>.md`, `pitfalls/<slug>.md` by symptom, self-bootstrapping `Justfile` recipes).

## Manifest (`package.json`)

`name: "pueue"`, `title: "Pueue"`, `author: "da-wei_lee"`, `license: "MIT"`, `categories: ["Developer Tools","Productivity"]`, `platforms: ["macOS"]` (mandatory — `menu-bar` is macOS-only).

| Command | mode | Notable |
|---|---|---|
| `tasks` | view | `query` text **argument** (the DSL escape hatch from root search); prefs `showDetail` (checkbox), `detailLogLines` (default 20) |
| `add-task` | view | optional `command` text argument |
| `quick-add` | no-view | required `command` + optional `label` arguments |
| `groups` | view | — |
| `queue-menu` | **menu-bar**, `"interval": "1m"` | prefs `titleCounts` (dropdown: running / running-queued / active / icon-only), `maxItemsPerSection` (7), `showGroups` (checkbox), `menuQuery` (e.g. `last 100`) |

Top-level preferences: `pueuePath` (empty = probe), `configPath` (→ `PUEUE_CONFIG_PATH`, also locates `task_logs/`), `confirmDestructive` (checkbox, default true).

Deps `@raycast/api ^1.104.23`, `@raycast/utils ^2.2.7`; dev `@raycast/eslint-config`, react 19, typescript 5.8, **`@types/node ^22`** (Raycast's runtime is Node 22 even though this shell has 24 via mise — typing against 24 lets you write APIs that throw at runtime). Scripts: `build: ray build`, `dev: ray develop`, `lint: ray lint`, `fix-lint: ray lint --fix`, `publish: npx @raycast/api@latest publish`.

**Never hand-write a `Preferences` interface.** `ray build` generates globally-declared `Preferences.Tasks` / `Arguments.Tasks` etc. with *required* fields, so a code default drifting from the manifest becomes a compile error. (The reference's own `lib/translate.ts` violates this — don't copy that part.)

## `src/lib/pueue/` — the transport seam

### `types.ts` — 1:1 with `pueue_lib` v4.0.4 serde

```ts
export type TaskResult =
  | "Success" | "Killed" | "Errored" | "DependencyFailed"
  | { Failed: number }            // exit code
  | { FailedToSpawn: string };

/** Externally tagged, recursive through Locked. No flat "status":"Running" exists. */
export type TaskStatus =
  | { Stashed: { enqueue_at: string | null } }     // note: enqueue_at
  | { Queued:  { enqueued_at: string } }           // note: enqueued_at
  | { Running: { enqueued_at: string; start: string } }
  | { Paused:  { enqueued_at: string; start: string } }
  | { Locked:  { previous_status: TaskStatus } }
  | { Done: { enqueued_at: string; start: string; end: string; result: TaskResult } };

export interface RawTask {
  id: number; created_at: string; original_command: string; command: string;
  path: string; envs: Record<string, string>; group: string;
  dependencies: number[]; priority: number; label: string | null; status: TaskStatus;
}
/** App-facing. `envs` is DROPPED at the parse boundary — never cached, never rendered. */
export type Task = Omit<RawTask, "envs">;

export interface Group { status: "Running" | "Paused" | "Reset"; parallel_tasks: number }
export interface State  { tasks: Record<string, Task>; groups: Record<string, Group> }
export type GroupMap = Record<string, Group>;   // `group --json` top level — DIFFERENT shape
export interface Snapshot { state: State; fetchedAt: number }
```

`envs` is stripped because `useCachedPromise` persists to the disk-backed `Cache` — caching it would write every secret in the submitting shell's environment to disk in plaintext, and it's ~2 MB per 400 tasks re-parsed every 60 s. One opt-in uncached accessor `taskEnvs(id)` backs a "Show Environment" action.

### `normalize.ts` — pure flatteners, no React, unit-testable

```ts
export type StatusKind = "stashed"|"queued"|"running"|"paused"|"locked"|"done"|"unknown";

export function statusKind(s: TaskStatus): StatusKind {   // "unknown" degrades, never throws
  return KINDS[Object.keys(s)[0]] ?? "unknown";
}
export function unwrapLocked(s: TaskStatus): TaskStatus {
  return "Locked" in s ? unwrapLocked(s.Locked.previous_status) : s;
}
export const underlyingKind = (s: TaskStatus) => statusKind(unwrapLocked(s));

/** Guarded — new Date(null) is 1970-01-01, not Invalid Date. */
export function parseTs(s: string | null | undefined): Date | undefined {
  if (!s) return undefined;
  const d = new Date(s);           // chrono RFC3339 w/ microseconds + numeric offset; V8 truncates to ms
  return Number.isNaN(d.getTime()) ? undefined : d;
}
export function enqueuedAt(s: TaskStatus): Date | undefined  // handles the enqueue_at/enqueued_at split
export function startedAt(s), endedAt(s), durationMs(t, now)
export function resultKind(r), exitCode(r)                   // exitCode only for { Failed: n }

/** Allowlist, not denylist — Killed / Errored / FailedToSpawn / DependencyFailed all count. */
export const isFailed = (t: Task) => {
  const k = resultKind(taskResult(t.status));
  return k !== undefined && k !== "success";
};
```

`format.tsx` layers presentation on top (kept separate so `normalize.ts` stays React-free): `statusIcon()` → one glyph per kind with a `tintColor` (running=Blue CircleProgress, paused=Orange, queued=SecondaryText Clock, locked=Purple Lock, success=Green CheckCircle, failed/killed=Red), `formatDuration()`.

### `errors.ts` — taxonomy + stderr normalizer

Kinds: `binary-not-found` · `config-missing` · `daemon-not-running` · `bad-query` · `bad-arguments` (exit 2, an extension bug) · `timeout` · `command-failed`. Predicates `isBinaryMissing(e)` / `isDaemonDown(e)` (true for both config-missing and socket-unreachable — same remedy).

```ts
// eslint-disable-next-line no-control-regex -- color_eyre emits raw SGR even when stderr is a pipe
const ANSI_SGR = /\[[0-9;]*m/g;

export function cleanStderr(raw: string): string   // strip ANSI, cut at "Location:"/"Backtrace omitted",
                                                    // drop the "Error:" header and eyre's "  0: " cause numbering
export function classify(detail: string, code: number | null): PueueErrorKind {
  if (/Failed to parse query/i.test(detail))              return "bad-query";
  if (code === 2 && /^error:/im.test(detail))             return "bad-arguments";
  if (/Couldn't find a configuration file/i.test(detail)) return "config-missing";
  if (/while connecting to daemon/i.test(detail))         return "daemon-not-running";
  return "command-failed";
}
```

Three verified stderr shapes to write fixtures against: the ANSI eyre report, plain `Pueue: The task to be followed doesn't exist.` (exit 1), and clap's `error: unexpected argument …` (exit 2).

### `binary.ts` — PATH resolution (the #1 hazard)

Modelled on brew's `paths.ts` + the reference's `resolveBinary()`. Probe order `/opt/homebrew/bin`, `/usr/local/bin`, `~/.cargo/bin`, `~/.local/bin`, `/usr/bin`, `/bin`. **The `pueuePath` preference is checked before the module cache** so changing it takes effect mid-session, and is `existsSync`-validated so a stale path falls through to the probe. `resolvePueued()` prefers the **sibling** of the resolved client (a Homebrew client driving a cargo daemon is version skew waiting to happen). `baseEnv()` passes `HOME` through so pueue finds its config dir, plus `PUEUE_CONFIG_PATH` when the preference is set — deliberately **not** setting `NO_COLOR`, which does nothing here. Also `taskLogPath(id)`, `resolveBrew()`, and `isBrewManagedDaemon()` (checks `~/Library/LaunchAgents/homebrew.mxcl.pueue.plist`).

### `transport.ts` — the seam is a data union, not argv

If mutations were `string[]`, a socket transport would have to parse argv back into intent. Model them as data:

```ts
export type Mutation =
  | { op: "add"; command: string; group?: string; label?: string; priority?: number;
      workingDirectory?: string; after?: number[]; delay?: string;
      stashed?: boolean; immediate?: boolean; escape?: boolean }
  | { op: "start"|"pause"|"kill"|"stash"|"enqueue"; ids?: number[]; group?: string; all?: boolean; /* +signal|wait|delay */ }
  | { op: "restart"; ids: number[]; inPlace?: boolean; stashed?: boolean; immediate?: boolean }
  | { op: "remove"; ids: number[] } | { op: "clean"; group?: string; successfulOnly?: boolean }
  | { op: "switch"; a: number; b: number } | { op: "parallel"; count: number; group?: string }
  | { op: "group-add"; name: string; parallel?: number } | { op: "group-remove"; name: string }
  | { op: "send"; id: number; input: string } | { op: "reset"; groups?: string[] };

export interface PueueTransport {
  readState(o?: StatusOptions): Promise<State>;      // status --json
  readGroups(s?: AbortSignal): Promise<GroupMap>;    // group --json — different shape, own parser
  readLogs(ids: number[], o?: LogOptions): Promise<LogMap>;
  mutate(m: Mutation): Promise<number | void>;       // add returns the new id
  followLog(id: number, lines: number, h: FollowHandlers): () => void;  // returns cancel
  probe(): Promise<{ version: string; reachable: boolean }>;
}
export function transport(): PueueTransport { return (active ??= createCliTransport()); }
```

### `cli-transport.ts` — the swappable implementation

```ts
/** Global flags go BEFORE the subcommand — `pueue status --color never` exits 2. */
function globalArgs() {
  return ["--color", "never", ...(configPath ? ["--config", configPath] : [])];
}
```

`argvFor(m)` is the only place argv is built. For `add`: flags first, then `--print-task-id`, then **`--` and the command as one argv element** — `pueue` joins the variadic `<COMMAND>...` with spaces and hands it to `sh -c` itself, so never shell-quote it, never `shell: true`. Timeouts: 10 s reads, 15 s writes; `maxBuffer` 64 MB on `status` (the `envs` payload). A shared `run()` wrapper turns `ENOENT` into `binary-not-found` and everything else into `new PueueError(classify(cleanStderr(stderr), code), …)`. `stripEnvs()` runs at the parse boundary, and `readState` guards against ever being handed `group --json`'s shape.

## Menu-bar command (`src/queue-menu.tsx`)

Data: `useCachedPromise(() => snapshot({query: prefs.menuQuery}), …, { keepPreviousData: true })`. Returning `{ state, fetchedAt }` puts the data **and its age in the same cache entry** — no second mechanism, no drift. `Cache`-backed, so the first paint after a Raycast restart is the last successful state rather than a blank menu.

**Title** — there is no badge API; the count *is* the title and `undefined` removes it (the brew idiom). Running first, since it's the only count that changes on its own:
```tsx
title={running > 0 ? String(running) : undefined}   // per the titleCounts preference
```
**Icon** — one monochrome template glyph, four tints, so the shape never moves in the menu bar: Red on error or any failed task, Orange when every group is paused, `SecondaryText` when showing stale cached data, `PrimaryText` normally. **Tooltip** carries the full sentence (`Pueue — 2 running, 5 queued, 1 failed` / `daemon not running` / `stale, last update 14:32`).

**Structure:** `Running` / `Queued` / `Failed` sections, each capped at `maxItemsPerSection` (default 7) with a trailing `…and N more` that `launchCommand`s Tasks — a 400-task queue must not become 400 menu items every 60 s. Per-task actions live inside per-task `Submenu`s titled `${id} · ${command}`, which both keeps the top level short and satisfies the rule that **no two identical `Item`s may sit at the same level** (their `onAction`s misfire). Optional `Groups` section with pause/resume + a parallelism submenu. Footer: `Add Task…`, `Open Tasks`, `Pause All` (⌥ alternate: let running finish), `Start All`, `Clean Finished`, then a disabled `Updated HH:MM` label and `Refresh`.

**No `confirmAlert` anywhere in the menu bar** — it presents in the Raycast window, which is closed when the menu is open; a silently-swallowed confirmation on a destructive action is unacceptable. Instead: Kill/Restart/Start/Pause/Stash/parallelism are one-click with a `showHUD` echo; **Remove and Clean are `alternate` (⌥) only**; Reset and Remove Group are **not offered at all** (view commands, behind `confirmAlert`). `showToast` falls back to `showHUD` when the window is closed, so `showFailureToast` is safe here.

**`isLoading`** drives straight from the hook — never unset (Raycast renders then immediately unloads), never stuck true (the whole React tree re-runs every tick until it goes false). `keepPreviousData` means no blank frame.

**Stale-restore mitigation** (trap 14): the disabled `Updated HH:MM` item makes staleness *visible* rather than misleading — this is the important one; plus a `Refresh` item; plus tinting `SecondaryText` and rewording the tooltip when a fetch fails but cached data exists.

**Degradation:** a `MenuBarExtra` can't render a `List.EmptyView`, and returning `null` removes the item entirely — wrong for someone who deliberately enabled it. Render a red-X `MenuBarExtra` whose items come from the shared `describeError()` descriptor.

`updateCommandMetadata({ subtitle: … })` on each successful fetch, so root search shows `2 running · 5 queued` or `Daemon not running`.

## View commands

### Tasks (`tasks.tsx`) — v1

Two `useCachedPromise`s with **separate abort refs** (state + groups), merged `isLoading`/`error` — the `look-up-word.tsx` pattern. A third `usePromise` fetches a log preview **for the selected row only**, on `onSelectionChange`, never per row.

- **Search is client-side** via per-item `keywords={[id, command, original_command, label, group, statusKind, resultKind, path]}` (the `history.tsx` idiom) — the whole state is already in memory, and the server-side DSL's `%=` only covers `command` and `label`. The DSL stays available as the `query` argument for launching from root search.
- **Group filter** is the single `searchBarAccessory` (Raycast allows one), seeded with a static `["All Groups","default"]` so it never paints empty, then swapped for the live map — preserving an unknown current value as a synthetic item so a deleted group doesn't silently reset the filter (lifted from `language-dropdown.tsx`).
- **Status filtering is by `List.Section`** (Running / Paused / Locked / Queued / Stashed / Failed / Done), not a second dropdown — grouping for free, and the search bar still filters across all of them.
- **Detail pane** (⌘⇧D) with `metadata` (id, status, result + exit code, group, label, priority, path, dependencies, created/enqueued/started/ended, duration) and `markdown` = the last N log lines fenced.

Actions: ⏎ Show Log · ⌘⏎ Follow Output (running only) · ⌘R Restart (⌥ alternate: Restart in Place — **say that it overwrites logs**) · ⌘K Kill · ⌘E Enqueue / ⌘S Stash (conditional on status) · ⌘⌫ Remove (disabled with an explanatory toast for running/paused — `pueue remove` refuses them) · ⌘C Copy Command · ⌘O Show in Finder · ⌘N Add Task · ⌘⇧R Reload. Destructive ones use `confirmAlert` with `rememberUserChoice` + `Alert.ActionStyle.Destructive`, gated on the `confirmDestructive` preference.

**Refresh after mutation — the load-bearing detail (trap 8).** `mutate`'s default `shouldRevalidateAfter: true` fires immediately, reads pre-change state, and *visually undoes* the optimistic update. So:

```ts
const RECONCILE_DELAY_MS = 700;   // estimate — verify against a real task in step 6
await st.mutate(mutate(m), {
  optimisticUpdate: (s) => applyMutation(s, m),   // pure, src/lib/optimistic.ts
  rollbackOnError: true,
  shouldRevalidateAfter: false,                   // ← the point
});
setTimeout(() => st.revalidate(), RECONCILE_DELAY_MS);
```

`src/lib/optimistic.ts` is a pure `State → State` function per `Mutation` (kill → Done+Killed, start → Running, pause → Paused, stash/enqueue swap, remove/clean delete, parallel sets the count; `add` is unpredictable — the id comes back from the CLI — so it returns `s` unchanged).

### Add Task (`add-task.tsx`) — v1

`Form` + `useForm`, `enableDrafts: true` so a half-typed pipeline survives a dismissal. Fields: `command` (`TextArea`, with `info` warning that a trailing `&` detaches the process so the task finishes instantly), `workingDirectory` (`FilePicker`, directories only, default from `LocalStorage`), `group` (live dropdown with a static `default` fallback), `label`, `priority` (integer-validated), `dependencies` (**`TagPicker` of current tasks** — far better than free-text ids), `delay`, `startMode` (**one dropdown: Queued / Stashed / Start immediately** — collapsing the mutually-exclusive `-s`/`-i` pair so both can't be ticked), `escape` checkbox.

On submit: toast with an "Open Tasks" primary action, persist cwd+group to `LocalStorage`, `popToRoot()`, and `launchCommand({name:"queue-menu", type: LaunchType.Background})` — the documented way to force a sibling's background refresh so the menu bar catches up in seconds rather than up to a minute. When pushed from Tasks (⌘N), thread an `onAdded → revalidate()` callback so the list is already fresh when the form pops.

### Groups (`groups.tsx`) — v1

Rows show status tag, `running/parallel_tasks` (`∞` for 0), and queued count. ⏎ opens Tasks filtered to that group; ⌘P/⌘S pause/resume (⌥: `--wait`, let running finish); ⌘⇧P set parallelism; ⌘N add group; ⌘K kill group — confirmation **must say "This also pauses the group"**; ⌘⌫ remove group — confirmation **must say "All N tasks will be moved to `default`"**; ⌘⇧⌫ reset. Parallelism comes from `group --json`, never `pueue parallel` (trap 10).

### Quick Add (`quick-add.tsx`) — v1, `no-view`, ~35 lines

Reads its `command`/`label` arguments, pulls group+cwd from `LocalStorage`, adds, `showHUD`, background-launches `queue-menu`. Highest value per line in the extension.

### Deferred

Edit Task (`pueue edit` TOML round trip), Send Input (`pueue send` — no way to know the process is reading stdin), Switch (needs a two-selection UI), env editor, saved queries.

## Log viewing — one source per situation

| Situation | Source | Why |
|---|---|---|
| Detail-pane preview, selected row | `pueue log <id> --json --lines 20` | One call returns task + output; bounded; pueue already blanks `envs` on this path |
| Full log page, finished task | Read `<pueue_dir>/task_logs/<id>.log`, tailing the last 512 KB | `read_local_logs: true` here; skips the JSON escape round trip and the `--full` RAM warning. Falls back to `pueue log --json --lines 2000` |
| Live tail, running task | `spawn(pueue, ["follow", id, "--lines", "200"])` | The only streaming surface pueue has; exits by itself when the task stops |

`<TaskFollowView>` follows `stream-view.tsx` (`useRef` accumulator + returning the cancel fn from `useEffect`) with three additions: a **200 ms flush interval** (don't re-render per 250 ms chunk), a **200 K char ring buffer** (a long-running task grows forever), and treating `onDone` as *success* — `follow` exiting means the task finished. Render output as a fenced `text` block with internal ``` escaped, so program output isn't interpreted as markdown.

## Onboarding / failure states

One descriptor, three renderers — this fixes the reference's duplicated `errorMarkdown()`:

```ts
export function describeError(e: unknown): ErrorDescriptor;  // single source of truth
export function ErrorEmptyView({ error })   // renders INSIDE a <List> — EmptyView isn't standalone
export function ErrorDetail({ error })      // full Detail
// the menu bar consumes describeError() directly
```

**A — binary not found:** `Copy "brew install pueue"` · `Copy "cargo install --locked pueue"` · `Open Extension Preferences` · `Open Pueue Documentation`. The Detail explains *why* (launchd, no shell rc) and lists the probe order.

**B — daemon not running** (both `config-missing` and socket-unreachable — same remedy): `Start Daemon` (conditional) · `Copy "brew services start pueue"` · `Copy "pueued -d"` · `Retry`. Include the cleaned stderr verbatim — the two messages are genuinely diagnostic (`Couldn't find a configuration file` = pueued has *never* run; `I/O error at path "…socket" while connecting to daemon` = it ran and stopped).

**One-click "Start Daemon" is offered only when the daemon is Homebrew/launchd-managed** (`resolveBrew()` succeeds **and** the plist exists) → `brew services start pueue`. Otherwise copy-only, with this note:

> Starting `pueued -d` from Raycast would make the daemon a child of Raycast's launchd process, so **every task it later runs would inherit Raycast's minimal environment** — no `~/.zshrc`, a bare `PATH`. Start it from a terminal or via `brew services`.

That's the same launchd-PATH problem as our own binary resolution, except it silently poisons every future task instead of failing loudly once. Guessing `pueued -d` also risks racing a launchd-managed daemon.

## Store readiness

512×512 PNG icon (+ SVG source) · monochrome `pueue-menubar.svg` always rendered with `tintColor` · `CHANGELOG.md` starting `## [Initial Version] - {PR_MERGE_DATE}` (literal placeholder) · `metadata/` ×5 at 2000×1250 captured with Raycast's *Save Screenshot* · committed `package-lock.json` (`npm ci` must work clean) · committed `raycast-env.d.ts`, regenerated after every manifest edit · `ray lint` + `ray build` clean (expect one `no-control-regex` suppression) · MIT `LICENSE`.

README order matters: (1) `brew install pueue` — the extension shells out, it queues nothing itself; (2) start the daemon, with the GUI-parent warning; (3) **background refresh is off by default for store installs** — run *Queue Menu Bar* once or enable it in command settings. This is the single most likely "it's broken" report; (4) binary-path preference + probe order + the launchd explanation; (5) tasks inherit the *daemon's* environment, not your shell's; (6) the `daemon.callback` notification recipe, presented as YAML *you* add to *your* `pueue.yml`, offered as a copy-to-clipboard action, stating explicitly that **the extension never writes to your pueue.yml**:

```yaml
daemon:
  callback: 'osascript -e "display notification \"{{ command }} → {{ result }}\" with title \"Pueue #{{ id }}\""'
  callback_log_lines: 10
```

Store rules explicitly allow "✅ Calling known system binaries"; "avoid asking users to perform additional downloads" is soft guidance, and **brew** (itself a `menu-bar` + `interval: 1m` extension), **colima**, **orbstack**, and **yabai** are all shipping precedents for requiring a pre-installed CLI/daemon. What a reviewer looks for is graceful onboarding — which is the section above.

## Build order

Each step is one commit, independently verifiable. `ray` ships inside `@raycast/api`, so `Justfile` recipes self-bootstrap with `([ -d node_modules ] || npm install) &&`.

| # | Step | Verify |
|---|---|---|
| 1 | Scaffold: manifest, tsconfig (copy the reference's verbatim), eslint, .gitignore, LICENSE, placeholder icon, empty `tasks.tsx` | `npm run build` writes `raycast-env.d.ts` containing `Preferences.Tasks`; `npm run lint` clean |
| 2 | `types.ts` + `normalize.ts` + a fixture with **one task per status variant, including `Locked` wrapping `Done{Failed:127}`** | `tsc --noEmit`; assert `enqueue_at: null` → `undefined` not 1970, `Locked` unwraps to `done`, `{Failed:127}` → exitCode 127 |
| 3 | `errors.ts` + `binary.ts` | Feed `cleanStderr` the three captured stderr samples — no ``, no `Location:`, no backtrace; `classify` maps all five; `resolvePueue()` → `/usr/local/bin/pueue` |
| 4 | `transport.ts` + `cli-transport.ts` + `index.ts`, **reads only** (`mutate` throws) | Call `status()` against the live daemon; confirm `readGroups()` returns `{default:{…}}` and does *not* go through the State parser |
| 5 | Tasks view, read-only | `npm run dev`, then open **from Raycast root search**. Queue real tasks in a terminal (`pueue add 'sleep 30'`, `pueue add 'false'`); confirm icons, durations, exit codes |
| 6 | Mutations: `argvFor` ×15, `optimistic.ts`, the `act()` helper | Kill a `sleep 30` from Raycast — the row must flip **immediately and stay flipped**. Flicker means `RECONCILE_DELAY_MS` is too low; that's the whole point of this step. Confirm `remove` on a running task surfaces pueue's refusal as a readable toast |
| 7 | `error-states.tsx` + wiring | Set `pueuePath` to `/nope`; `brew services stop pueue` **and restart it after** |
| 8 | Log views (static + follow) | `pueue add 'for i in $(seq 1 200); do echo line $i; sleep 0.2; done'`, follow from Raycast, confirm live updates and self-termination |
| 9 | Add Task + Quick Add | Submit with cwd/group/label/priority/dependency and each `startMode`; test a command with `&&`, quotes, `$HOME` — confirm no double-escaping |
| 10 | Groups view | Cross-check every action against `pueue group --json` in a terminal |
| 11 | Menu-bar command | Open once to register. Verify title hides at zero, an action HUDs, it self-refreshes ~1 min later, the daemon-down menu is red rather than absent, **and quit/relaunch Raycast** to check the restored `Updated` time |
| 12 | Cross-command refresh (`launchCommand` background) | Quick Add a task; menu-bar count bumps within seconds, not a minute |
| 13 | Docs + knowledge harness (README, CHANGELOG, `docs/`, 5 `pitfalls/`, `TODO.md`, `backlog/`, `Justfile`) | `just --list`; each recipe runs from a `node_modules`-less checkout |
| 14 | Store polish: real icon, 5 screenshots, final lint/build | `rm -rf node_modules && npm ci && npm run build` (proves the lockfile). **Do not run `npm run publish`** — deferred by decision |

**Testing note for `pitfalls/raycast-launchd-path-pueue-not-found.md`:** a terminal inherits your full PATH and finds `pueue` by bare name, so the launchd bug is invisible from `npm run dev`'s console. Every step marked "from Raycast" must go through Raycast root search or the menu bar.

## Backlog (seed `TODO.md`)

| Tag | Item |
|---|---|
| `[?/L]` | **CBOR unix-socket transport** — implement `PueueTransport` against `pueue_<user>.socket` with the shared-secret handshake + 8-byte BE framing. Kills ~28 ms/read and unlocks real push (`Request::Stream`). Cost: reimplementing `pueue_lib`'s wire protocol in TS and re-pinning it every pueue release. Prior art: `beeequeue/pueue-ui` |
| `[?/M]` | **AI tools (`tools[]`)** — "what's running", "queue this", "why did task 12 fail". Raycast **Pro**-gated, so it can't be the primary UX |
| `[S]` | **Callback-driven notifications** — a documented `daemon.callback` firing a `raycast://` deeplink so a finished task refreshes the menu bar instantly. README + copy action only |
| `[M]` | `pueue edit` round trip · **Remote profiles** (`--profile` switcher; needs a real YAML dep) · frecency-sorted command history for Add Task |
| `[S]` | `pueue send` form · dependency graph in the task detail · `pueue wait` drain notifier · group-scoped reset |

## Risks

1. **`confirmAlert` from a menu-bar command is unverified** — the design avoids it entirely; verify in step 11, though ⌥-alternates remain the better affordance either way.
2. **`RECONCILE_DELAY_MS = 700` is an estimate**, not a measurement. Step 6 exists to calibrate it; if it flickers, escalate to a short poll (300/700/1500 ms, stop on first change).
3. **Version skew** — this models v4.0.4. `statusKind` returns `"unknown"` rather than throwing, and `probe()` warns below 4.0. Per the reference's own pitfall: never probe capability by exit status, check the *shape* of the output.
4. **Large queues** — stripping `envs` fixes retention but not parse cost; `menuQuery` (`last 100`) is the escape hatch.
5. **Store review pushback** on requiring a daemon. Precedent + onboarding mitigate it; fallback is private/org store or GitHub-hosted local install.
6. **`backlog/`, `pitfalls/`, `TODO.md`, `Justfile` ride along on publish** and add review noise. Escape hatch if a reviewer objects: a `just store-export` recipe that rsyncs a clean subset to `/tmp` and publishes from there. Don't do it pre-emptively.
