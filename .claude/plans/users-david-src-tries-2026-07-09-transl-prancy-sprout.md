# v0.3.0 — group observability, batch actions, and one real bug

## Context

Two things prompted this.

**The bug.** Selecting a connection that can't be reached makes the menu bar
render *the previous connection's* queue as if it were the new one's — your
screenshot shows `nas` selected with `default — running (0/1)` underneath and no
hint of failure. Cause, read out of `node_modules/@raycast/utils/dist/main.js`
around line 759: `useCachedPromise` keys its cache on `hash(args)`, but
`keepPreviousData: true` falls back to a **key-independent** `laggyDataRef` when
the new key's cache entry is empty. Once any read has succeeded,
`lastUpdateFrom.current === "promise"` forever, so a *failed* read on a new
connection still returns the last good data from whichever connection produced
it. The menu bar then never enters its `if (!data)` branch, so the error is
invisible — and the design's own promised mitigation ("tint `SecondaryText` and
reword the tooltip when a fetch fails but cached data exists") was never built:
`menuIcon()` in `src/queue-menu.tsx:603` takes no error at all.

**Groups tell you nothing.** `wf — running (0/1)` is `running/parallel_tasks`.
With six failed tasks sitting right above it in the same menu, the group row
reports `0/1` — the one number that never moves. The original motivation was
batch-submitting a series of tasks into a group and then watching *that batch*,
which is precisely what the row can't show. `pqsum`
(`~/.local/share/chezmoi/dot_dotfiles/bin/executable_pqsum`, `GroupRec` at line
154, `render_text` at 426) already models the right numbers: done/total, a bar,
avg duration, ETA, elapsed, and the failed ids.

**Decided in this session:** no fleet view (drop it from TODO — it would make the
tool more complex than the problem). Reset stays out of the menu bar as a plain
item but is offered there behind an **unconditional ⌥**, alongside jumps into the
Groups view. Groups gets a detail pane. `v0.2.1` is cut first.

## Verified before planning

| Claim | How |
| --- | --- |
| `status --json` returns the **identical** groups map to `group --json` | ran both; byte-identical `{"default":{"status":"Running","parallel_tasks":1}}` |
| `status --json --group X` filters **tasks only**, never the groups map | `--group default` → 1 task, all groups; `--group nope` → 0 tasks, all groups, exit 0 |
| `pueue restart` has `-g/--failed-in-group <GROUP>` and `-a/--all-failed` | `pueue restart --help` |
| `pueue clean` has `-g/--group` — and `argv.ts:82` already emits it | `pueue clean --help` |
| `getProgressIcon(progress, color)` exists in `@raycast/utils` | `dist/types.d.ts:1176` |
| `MenuBarExtra.Item` accepts `subtitle` and `tooltip` | `@raycast/api/types/index.d.ts:5861` |

The fixture at `src/lib/fixtures/state.json` already covers what the new pure
module needs: three groups (`default` Running/1, `build` Paused/4, `gpu`
Running/**0 = unlimited**, the ETA divide-by-zero case), and every status variant
including `Locked` wrapping both `Done` and `Queued`.

---

## Step 0 — tag `v0.2.1` at current HEAD

Before any of the below. `v0.2.0` currently describes a remote feature that
supported one connection, could hang 30 s, could strand you on a broken remote,
and ignored the confirmations preference. Annotated tag covering: the `;`
separator, the SSH `ConnectTimeout`, the menu bar escape routes, the ⌥ fix.

## Step 1 — one read per view

`status --json` is a strict superset of `group --json` (verified above), so the
paired hooks are a spare subprocess per revalidate *and* a second cache entry
that can disagree with the first.

Collapse in `src/tasks.tsx:150`, `src/groups.tsx:62`, `src/add-task.tsx:77` —
drop each `readGroups` hook and read `state.data.groups` instead.
`groupNames()` in `src/lib/group-dropdown.tsx:25` already takes a `GroupMap`,
so the dropdown needs no change.

Deletes `applyGroupMutation` (`src/lib/optimistic.ts:258`) and `actOnGroups`
(`src/lib/actions.tsx:172`); Groups switches to `actOnTasks`, whose
`applyMutation` already covers every group op **and** their task-level effects —
which the new progress numbers depend on. Drop the matching `dev-check.ts`
assertions (`applyGroupMutation`, around line 679) and keep the `State` ones.

## Step 2 — snapshots carry their connection

Make the cross-connection bleed unrepresentable rather than patched per call
site. In `src/lib/pueue/types.ts` and `index.ts:99`:

```ts
export interface Snapshot { state: State; fetchedAt: number; connection: string }

export async function snapshot(o?: StatusOptions): Promise<Snapshot> {
  return {
    state: await status(o),
    fetchedAt: Date.now(),
    connection: (o?.connection ?? defaultConnection()).name,
  };
}
```

All three views move from `status()` to `snapshot()` and gate on it:

```ts
const snap = state.data?.connection === conn.connection.name ? state.data : undefined;
```

`undefined` here means "nothing for *this* daemon yet", so the existing
`error && !data` branch takes over and renders `ErrorEmptyView` / `ErrorMenu`.
While a switch is in flight the list is empty with `isLoading` — deliberately
losing `keepPreviousData`'s no-blank-frame benefit across connections, because
that benefit is exactly what showed another machine's queue.

Keep `keepPreviousData` for same-connection refreshes; the guard only rejects a
*different* connection's payload.

Also: `ErrorMenu` must not be rendered with `error === undefined` (it renders
`firstLine(String(undefined))` → the literal string "undefined"). Gate it
`isLoading || !error ? spinner : <ErrorMenu/>`.

## Step 3 — the menu bar admits when it's stale

The mitigation the design promised. In `src/queue-menu.tsx`, when there is data
*for this connection* and `error` is set:

- `menuIcon()` gains an `error` arm → `Color.SecondaryText`
- `tooltip()` says `stale · last update HH:MM` instead of the counts
- a first section renders `describeError(error, connection).shortTitle` plus
  Retry and the descriptor's own actions — the menu bar equivalent of
  `StaleBannerItem` (`src/lib/error-states.tsx:414`)

This is the fourth instance of the recurring bug class in this repo: the escape
route or the error report missing from the degraded path.

## Step 4 — `src/lib/group-summary.ts` (pure)

New module beside `normalize.ts`/`optimistic.ts`, React- and I/O-free so
`just verify` can assert it. Modelled on pqsum's `GroupRec`; every field is
computed from helpers that already exist in `src/lib/pueue/normalize.ts`
(`isRunning`, `isQueued`, `isPaused`, `isStashed`, `isFailed`, `isSuccess`,
`durationMs`, `startedAt`, `endedAt`).

```ts
export interface GroupSummary {
  name: string;
  status: Group["status"];
  parallel: number;              // 0 = unlimited
  total: number;
  running: number; queued: number; paused: number; stashed: number;
  succeeded: number; failed: number;
  finished: number;              // succeeded + failed
  progress: number;              // 0–1, 0 for an empty group (never NaN)
  avgMs?: number;                // mean wall clock of finished tasks
  etaMs?: number;                // see below
  elapsedMs?: number;            // first start → last end (or now)
  failedIds: number[];
}

export function summarizeGroups(groups: GroupMap, tasks: Task[], now?: number): GroupSummary[]
export function summarizeAll(summaries: GroupSummary[]): { finished; total; failed; running }
export function progressBar(progress: number, width?: number): string   // "████░░░░░░"
```

Decisions worth writing down in the module comment:

- **`total` is every task in the group.** pueue has no batch concept; `pueue
  clean` between batches is the only boundary that exists. Inventing one ("since
  the group last went idle") would be a guess presented as a fact.
- **ETA excludes stashed tasks.** A stashed task waits on a human, not on the
  queue, so `pending = running + queued + paused`; `etaMs = pending * avgMs /
  max(parallel, 1)` — `parallel === 0` means unlimited, and dividing by zero is
  how the `gpu` fixture catches this.
- **ETA needs ≥2 finished samples.** One sample is a guess wearing a number's
  clothes. `undefined` renders as `—`, which is honest.
- `finished` counts `Done` regardless of result, matching pqsum's `done` — a
  failed task is finished.

Assertions in `src/lib/dev-check.ts` against the fixture: the `gpu` group's
unlimited parallelism does not produce `Infinity`/`NaN`; `build`'s
`Locked{Done{…}}` task counts as finished (it must go through `underlyingKind`,
not `statusKind`); an empty group is `progress === 0` not `NaN`; a group with one
finished task has `avgMs` but no `etaMs`.

## Step 5 — Groups view: ring, summary line, detail pane

`src/groups.tsx`, using the summaries from step 4.

- **icon** → `getProgressIcon(s.progress, healthColor(s))`, replacing
  `groupIcon()` for this view. Tint carries the state the ring can't:
  Red when `failed > 0`, Orange when the group is Paused, Blue when `running >
  0`, Green when complete and clean, `SecondaryText` when empty. The Paused case
  matters — the ring must not silently drop the pause indicator the Play/Pause
  glyph gave.
- **subtitle** → `5/12 done · 1 running · 2 failed`, zero terms omitted.
- **accessories** → `~3m left` when `etaMs`, then the existing status tag.
- **section subtitle** → the overall, free:
  `<List.Section title="Groups" subtitle="13/26 done · 6 failed">`.
- **detail pane**, new command preference `showDetail` (checkbox, default true —
  groups are few, so the pane costs no scrolling), toggled with ⌘⇧D exactly as
  in `src/tasks.tsx:562`. Metadata rows: Progress `6/6 · 100%`; a
  `Metadata.TagList` breakdown (Running/Queued/Paused/Stashed/Succeeded/Failed,
  reusing the colours from `src/lib/format.tsx`); Daemon `Running · 1 at a time`
  (`∞` for 0); Average duration; Estimated remaining; Elapsed; and the failed
  ids as `#4 #5 #6`. `formatDuration()` in `format.tsx:102` already formats all
  three durations.

## Step 6 — batch actions

**New mutation shape.** `src/lib/pueue/transport.ts:61` — `restart`'s `ids`
becomes optional, plus `failedInGroup`:

```ts
| { op: "restart"; ids?: number[]; failedInGroup?: string; allFailed?: boolean;
    inPlace?: boolean; stashed?: boolean; immediate?: boolean }
```

`argv.ts:55` emits `--failed-in-group <name>` / `--all-failed` before the
explicit ids; guard `m.ids ?? []`. `optimistic.ts:195` gains the in-place branch
for a group: flip that group's failed tasks to `Queued`. Not-in-place stays
`return state` — the new ids are unknowable. Add argv assertions to
`dev-check.ts` alongside the existing 15.

**Groups view — Manage section:**

- `Restart Failed (N)` ⌘⇧R and `Restart Failed in Place (Overwrites Logs)` ⌘⌥R,
  mirroring the per-task pair at `tasks.tsx:418`. Hidden when `failed === 0`.
  Confirmation names the count.
- `Clean Finished in Group` — `{op:"clean", group:name}`, plus a
  successful-only variant. Confirmation says how many and that logs go too.

**Menu bar group submenu** (`queue-menu.tsx:277`):

```
wf ▸  [████░░░░] 100% · 5 failed          ← disabled summary row
      ──────────────────────────
      Pause Group
      Parallelism            ▸
      Restart Failed (5)     ▸            ← new-task / in-place
      Hold ⌥ to Clean Finished            ← follows the confirmations pref
      Hold ⌥ to Reset Group               ← ALWAYS ⌥, see below
      ──────────────────────────
      Show Tasks in Group
      Open Groups…
```

Reset is the one item whose ⌥ gate ignores the confirmations preference. That is
not the exception I removed last session — the Groups view already treats reset
as categorically worse than the rest, with `// No rememberChoice: this one
should ask every time` (`groups.tsx:365`). It kills every task in the group,
deletes them, and deletes their logs. The unconditional ⌥ in the menu bar is the
same policy in the only form available where no dialog can render, and both the
item title and the preference description will say so.

## Step 7 — parallelism beyond the presets

`PARALLEL_CHOICES` (`groups.tsx:52`) is `[1,2,3,4,6,8,12,0]` — a simplicity
compromise that runs out on a many-core workstation, as you spotted.

Extend it to `[1,2,3,4,6,8,12,16,24,32,0]` and add a trailing `Custom…` item
that pushes a small form (the `AddGroupForm` pattern at `groups.tsx:385`) with
one integer-validated field, `0` documented as unlimited. Deliberately **not**
seeding presets from `os.cpus().length`: for a remote connection that is the
wrong machine's core count, and a plausible-looking wrong number is worse than a
static list. Menu bar keeps its short list plus a `More…` item opening Groups —
free text isn't enterable in a menu.

## Step 8 — menu bar group rows

Submenu title becomes `${name} · ${finished}/${total}` plus ` · N failed` and
` · paused` when they apply, `${name} · idle` when the group is empty. Submenu
icon gets the same `getProgressIcon` ring. First child is the disabled summary
row `[████░░░░░░] 40% · 1/2 slots · ~4m left`, in the same slot where
`TaskSection` already puts `statusTag(task)` (`queue-menu.tsx:452`).

## Step 9 — per-connection counts, opt-in

New `queue-menu` preference `connectionCounts` (checkbox, default **off**).
Rather than a section per connection (which would explode the menu), the
existing Connection section rows gain a `subtitle`:

```
Connection
  Local              2 running · 1 failed
  lab (david_ubuntu) 5 running
✓ nas (ts_nas)       unreachable
```

Reads run through `Promise.allSettled` in one extra `useCachedPromise` keyed on
the connection list, so a dead host degrades its own row and never the menu.
Skipped entirely when there is one connection or the preference is off. The menu
bar title stays the selected connection's count — one number in the menu bar,
unambiguous.

## Step 10 — dependency graph in the task detail

`tasks.tsx:674` renders `dependencies[]` as `#3, #7`. Replace with one
`Metadata.TagList` item per dependency, coloured by the depended-on task's live
status via `statusColor()`/`statusTag()` — the whole `State` is already in hand,
so this is lookup, not I/O. An id no longer in the state renders as `#7 (gone)`,
the same treatment `GroupDropdown` gives a vanished group.

## Step 11 — docs and knowledge harness

- `pitfalls/usecachedpromise-keeps-data-across-cache-keys.md` — symptom-titled,
  with the `laggyDataRef` mechanism and the connection-tagging fix. This is a
  library behaviour nothing in the type system warns about.
- `docs/groups.md` + `docs/groups.zh-TW.md` — what the numbers mean, why `total`
  is the whole group, why the ETA is `~` and sometimes `—`. Add to `mkdocs.yml`
  nav via `add-docs-page.sh`.
- `TODO.md` — delete the fleet-view entry with a line saying why (kept the tool
  simple; per-connection subtitles cover the "how are my machines" question at a
  fraction of the complexity). Move the shipped v0.3.0 items out.
- `CHANGELOG.md`, then tag `v0.3.0`.

---

## Verification

`just check` (typecheck → verify → lint → build) must be green **before** every
commit; the gate has been pushed red twice, both times Prettier-only.

Per step, against a real daemon:

1. **Step 1** — `pueue group --json` and `pueue status --json | jq .groups` still
   agree; Groups and the Tasks dropdown render identically to today with one
   fewer spawn.
2. **Step 2, the actual bug** — configure a deliberately dead host
   (`dead | nosuchhost.invalid`), select it in the menu bar. Expect the red
   `ErrorMenu` with the connection switcher, **not** a `default` group. Then
   switch back to Local and confirm it recovers. Repeat in Tasks and Groups.
3. **Step 3** — with Local selected and data on screen, `brew services stop
   pueue`, wait for the interval. Expect a grey icon, a warning row, and a
   `stale` tooltip — not a queue that looks alive. `brew services start pueue`
   after.
4. **Step 4** — `just verify`; assertion count rises from 224. The `gpu`
   (unlimited) and `build` (`Locked{Done}`) cases are the ones that matter.
5. **Steps 5–8** — `just fixtures` seeds five tasks across statuses; add a second
   group and submit a batch of ~6 (some `false`) to watch the ring, the ETA, and
   `Restart Failed` on a real failure set. Verify `restart --failed-in-group`
   with `--not-in-place` and with `--in-place` against the daemon **before**
   wiring the UI — `--help` lists both flags but their interaction is unverified.
6. **Step 9** — with `lab` reachable and `nas` not, confirm one dead host leaves
   the other subtitles intact and the menu still opens promptly (the 5 s
   `ConnectTimeout` from `ssh.ts` bounds it).
7. **Step 10** — `pueue add --after N` a chain, confirm the tags track status and
   that removing the dependency renders `(gone)` rather than vanishing.

Store-facing, once the above is green: `just dist`, `just store-export`,
`just preflight`. Screenshots remain the one unautomatable item.
