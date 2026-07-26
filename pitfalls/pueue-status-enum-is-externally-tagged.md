# `task.status` is an object, and `status.Done.result` is sometimes a string

## Symptoms (grep this section)

```text
task.status.toLowerCase is not a function
Cannot read properties of undefined (reading 'result')
status shows "[object Object]"
a stashed task's date renders as 1/1/1970
every finished task looks successful
```

## Cause

pueue **4.0.0 moved task timestamps and results inside the status enum**, and
serialises it with serde's default external tagging. There is no flat
`"status": "Running"` anywhere in v4 — every v3-era example online is wrong.

```jsonc
{"Queued":  {"enqueued_at": "..."}}
{"Running": {"enqueued_at": "...", "start": "..."}}
{"Done":    {"enqueued_at": "...", "start": "...", "end": "...", "result": <TaskResult>}}
{"Locked":  {"previous_status": { /* another TaskStatus */ }}}
{"Stashed": {"enqueue_at": null}}
```

Four separate traps live in that block:

1. **`Locked` is recursive.** It wraps the status the task returns to when the
   edit finishes. Anything that filters on status must unwrap first, or a locked
   running task disappears from the Running section.
2. **`enqueue_at` vs `enqueued_at`.** `Stashed` uses the first — nullable, and
   pointing *forwards* to when the task will be enqueued. Every other variant
   uses the second, pointing backwards. A one-letter typo yields `undefined`
   silently.
3. **`new Date(null)` returns 1970-01-01, not `Invalid Date`.** Combined with
   trap 2, an unguarded parse renders every plain stashed task as 1970.
4. **`TaskResult` mixes bare strings with single-field objects** — `"Success"`
   and `"Killed"` but `{"Failed": 127}` and `{"FailedToSpawn": "..."}`. Reading
   `result` as a string works for exactly half the cases.

## Fix

Nothing outside `src/lib/pueue/normalize.ts` touches the raw enum.
`underlyingKind()` unwraps `Locked`; `parseTs()` guards the null; `enqueuedAt()`
handles the spelling split; `resultKind()` handles both result shapes.

**Failure detection is an allowlist** — anything terminal that isn't `"Success"`:

```ts
export function isFailed(t: Task): boolean {
  const k = resultKind(taskResult(t.status));
  return k !== undefined && k !== "success";
}
```

A denylist of `["Failed", "Killed"]` misses `FailedToSpawn`, `Errored`,
`DependencyFailed`, and whatever pueue adds next.

`statusKind()` returns `"unknown"` rather than throwing on an unrecognised tag,
so a future pueue degrades instead of crashing.

## Guard

`src/lib/fixtures/state.json` carries one task per variant, **including a
`Locked` wrapping a `Done` with `{"Failed": 127}`**, and `just verify` asserts
every accessor against it.
