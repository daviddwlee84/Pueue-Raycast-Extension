# Data, cache, and state

Read this when wiring `useCachedPromise`/`usePromise`/`mutate`, when an action
visibly reverts and re-applies, or when deciding what to persist and under which
key.

## Table of contents

- [Which storage for which value](#which-storage-for-which-value)
- [useCachedPromise](#usecachedpromise)
- [What lands on disk](#what-lands-on-disk)
- [Mutations, optimistic updates, and the ack lag](#mutations-optimistic-updates-and-the-ack-lag)
- [Measuring your own backend](#measuring-your-own-backend)
- [Writing an optimistic updater](#writing-an-optimistic-updater)
- [Cached data that has become a lie](#cached-data-that-has-become-a-lie)
- [Forms](#forms)

---

## Which storage for which value

| Need | Use | Notes |
| --- | --- | --- |
| Remote data, want it on screen instantly next launch | `useCachedPromise` | disk-backed, keyed by the argument array |
| Remote data, no cache wanted | `usePromise` | same shape, nothing persisted |
| View state read synchronously during render | `useCachedState` | sync; survives relaunch |
| A value another **command** must read | `LocalStorage` | async; the only cross-command channel |
| Ad-hoc key/value from non-hook code | `Cache` | what `useCachedPromise` writes to |

`useCachedState` and `LocalStorage` are not interchangeable. A filter the current
view needs during its first render must be `useCachedState`; the last-used group
that a `no-view` sibling command reads must be `LocalStorage`.

**Scope every key by whatever the value belongs to.**

```ts
const [group, setGroup] = useCachedState(`tasks.group:${account.name}`, ALL);
await LocalStorage.setItem(`add.cwd:${account.name}`, cwd);
```

A single shared key meant that switching to another machine left the list
filtered by a group that only existed on the first one — so it showed nothing,
with no error. Scoping the key makes that mistake unrepresentable rather than
merely corrected. Share a key deliberately, and only when two surfaces must
agree (a selected account is a good example: the menu bar and the list must never
disagree about which one they are showing).

## useCachedPromise

```ts
const stateAbort = useRef<AbortController>(null);
const groupsAbort = useRef<AbortController>(null);

const state = useCachedPromise(
  (group: string, query: string, account: string) =>
    read({ group, query, account, signal: stateAbort.current?.signal }),
  [group, query, account.name],
  { keepPreviousData: true, abortable: stateAbort },
);
```

- **Everything the fetcher depends on is an argument.** The argument array is the
  cache key *and* the refetch trigger. A value captured from the closure changes
  without invalidating anything, so the hook keeps serving the previous
  account's data with no visible error. This is the single most common wiring
  mistake.
- **One `abortable` ref per independent read.** Sharing one means a superseded
  status read cancels an unrelated in-flight groups read.
- **`execute: false` disables the fetch.** Use it for anything conditional —
  a per-selection detail that only loads when the pane is open, and only for a
  row that can have one:

  ```ts
  const preview = useCachedPromise(
    async (id: number, account: string) => readLog(id, account),
    [selectedId, account.name],
    { execute: showDetail && canHaveLog, keepPreviousData: false },
  );
  ```

  `keepPreviousData: false` there is deliberate: keeping it would show the
  previous row's log under the newly selected row for a frame.

- **Do not ask when you already know the answer.** If a row has never run, it has
  no log — gate the call on that rather than spending a subprocess spawn per
  selection change to be told so.
- **`isLoading` is `true` on the first load only** when `keepPreviousData` is on
  and a cached value exists. That is usually what you want for a list.

## What lands on disk

`useCachedPromise` writes the resolved value to Raycast's **disk-backed
`Cache`**, in plaintext. Everything in the object you return is on disk.

So strip secret-bearing and bulky fields **in the parse function**, at the
boundary, before anything can cache them:

```ts
/** The env snapshot would put every variable in the submitting shell on disk
 *  in plaintext, because useCachedPromise persists to Raycast's Cache. */
export type Task = Omit<RawTask, "envs">;
function strip(raw: Record<string, RawTask>): Record<string, Task> { /* delete lean.envs */ }
```

The size argument alone justifies it: six trivial records measured 53,595 bytes
with the environment snapshot and 2,509 without — 21×. The security argument
settles it. If some view genuinely needs the field, fetch it on demand for one
record, from an explicit action, never from a render path.

## Mutations, optimistic updates, and the ack lag

`mutate()` revalidates on success **by default**. That is wrong for any backend
that acknowledges a request before its own update loop applies it — which is most
daemons, many queues, and plenty of HTTP APIs behind a write-behind cache.

The symptom is unmistakable once you know it: the row shows the new state, snaps
back to the old one, then becomes the new one again a second later.

```ts
const RECONCILE_FAST_MS = 400;    // clears the measured worst case
const RECONCILE_SETTLE_MS = 1500; // covers a loaded backend

await state.mutate(runMutation(m), {
  optimisticUpdate: (data) => predict(data, m),
  rollbackOnError: true,
  shouldRevalidateAfter: false,     // <- the load-bearing line
});
setTimeout(() => state.revalidate(), RECONCILE_FAST_MS);
setTimeout(() => state.revalidate(), RECONCILE_SETTLE_MS);
```

Two reads rather than one pessimistic read: the fast one keeps the action feeling
immediate, and the settle one means a mis-tuned first delay never becomes
visible. If you also nudge a sibling menu-bar command, **delay that by the same
amount** — a `launchCommand` fired immediately makes the menu bar re-read exactly
the stale state you just worked around.

## Measuring your own backend

`400` and `1500` are measurements of one daemon on one machine. Take five
samples of your own before hardcoding anything:

```bash
for i in 1 2 3 4 5; do
  id=$(mytool add -- 'sleep 60')                     # something to mutate
  start=$(python3 -c 'import time;print(time.time())')
  mytool kill "$id" >/dev/null                        # returns when ACKED
  while ! mytool status --json | grep -q '"Killed"'; do :; done
  python3 -c "import time;print(f'{(time.time()-$start)*1000:.0f} ms')"
done
```

Take the max, round up, and use roughly 1.5× it for the fast delay. If the spread
is wide, that is itself the finding: pick a fast delay for the median and let the
settle read cover the tail.

## Writing an optimistic updater

Keep it **pure and total**: no I/O, no React, never throws, never mutates its
input.

```ts
export function predict(data: State | undefined, m: Mutation): State | undefined {
  if (!data) return data;
  switch (m.op) {
    case "kill":  return mapIds(data, m.ids, (t) => ({ ...t, status: "Killed" }));
    case "pause": return mapIds(data, m.ids, (t) => ({ ...t, status: "Paused" }));
    // Not predicted: a free slot needs the scheduler.
    case "start": return data;
    // Not predicted: the new id is unknowable until the server assigns it.
    case "add": case "restart": return data;
  }
}
```

**Return the input unchanged whenever the outcome is unknowable.** A
server-assigned id, a scheduler decision, a queue reordering — a wrong prediction
flickers exactly like no prediction *and* is also wrong, so it is strictly worse.

Because it is pure, it is assertable without Raycast:

```ts
check("starting a queued item is NOT predicted", kindOf(predict(s, start), 3), "queued");
check("the input state is never mutated in place", s === snapshot, true);
```

## Cached data that has become a lie

`useCachedPromise` keeps serving its last successful result when a fetch fails.
That is the right default for a flaky read. It is the wrong default when the
failure is **structural** — the daemon stopped, the token expired, the host is
unreachable — because then the cached data is not a moment stale, it is a
snapshot of a system nobody can see any more, rendered as if live.

| Situation | Render |
| --- | --- |
| first read failed, no data at all | `<List><ErrorEmptyView/></List>` — the whole screen |
| structural failure, cached data present | a persistent banner row at the top of the list |
| transient failure | the ordinary toast, nothing else |

```tsx
const stale = error !== undefined && describeError(error).structural;
{stale ? <List.Section title="Connection"><StaleBanner … /></List.Section> : null}
```

Find these by actually stopping the backend and looking, not by reading code.

## Forms

- `useForm` from `@raycast/utils` handles validation and submission; a plain
  controlled form is fine too, with `error` plus clear-on-change:

  ```tsx
  error={priorityError}
  onChange={(v) => { setPriority(v); if (priorityError) setPriorityError(undefined); }}
  ```

- **`enableDrafts` on the `<Form>`** so a half-filled form survives a dismissal.
- **`info=` carries the domain warning** — "a trailing `&` detaches the process
  and the task finishes instantly" belongs on the field, not in a README.
- **One dropdown beats two checkboxes when the underlying flags are mutually
  exclusive.** Two checkboxes let the user tick both and produce an invalid call.
- **Name the submit button after its target**, not "Submit": `Queue on lab-01`
  vs `Queue Locally`. A dropdown further up the form is easy to miss, and "I ran
  it on the wrong machine" is a mistake you notice much later.
- After a successful submit: `pop(); await popToRoot();`.
- Remember last-used values in `LocalStorage`, scoped per account.
