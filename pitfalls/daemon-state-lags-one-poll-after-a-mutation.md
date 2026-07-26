# A killed task flips back to Running, then to Done a moment later

## Symptoms (grep this section)

An action appears to work, undo itself, then work again:

- kill a task → the row shows **Killed** → snaps back to **Running** → becomes
  **Killed** again a second or a minute later
- pause a group → the tag flickers Paused → Running → Paused
- generally: any optimistic update visibly reverting

## Cause

**The pueue daemon acknowledges a request before its own update loop applies
it.** `useCachedPromise`'s `mutate()` revalidates immediately on success by
default, so the follow-up read lands in that window and returns pre-change
state — which overwrites the optimistic update with the truth-as-of-a-moment-ago.

Measured on this machine: kill a running task, then poll `status --json` until
it reports `Done`, five trials.

```text
min 278 ms   median 284 ms   max 297 ms
```

The `kill` call itself returns in ~22 ms. So there is a reliable ~280 ms window
in which the daemon has said yes and still reports the old state.

## Fix

`src/lib/actions.tsx`:

```ts
await state.mutate(runMutation(mutation), {
  optimisticUpdate: (data) => optimistic(data, mutation),
  rollbackOnError: true,
  shouldRevalidateAfter: false,   // the load-bearing line
});

setTimeout(() => state.revalidate(), RECONCILE_DELAY_MS);   // 400 ms
setTimeout(() => state.revalidate(), RECONCILE_SETTLE_MS);  // 1500 ms
```

Two reads rather than one pessimistic read: 400 ms clears the measured worst
case and keeps actions feeling immediate, and 1500 ms covers a loaded daemon so
a mis-tuned first delay never becomes visible.

The menu bar nudge is delayed by the same 400 ms, for the same reason — a
`launchCommand` fired immediately would have the menu bar re-read exactly the
stale state we just worked around.

## If it flickers anyway

Raise `RECONCILE_DELAY_MS`. Re-measure first rather than guessing; the script
that produced the numbers above is a loop of `pueue add 'sleep 300'`, wait for
Running, `pueue kill`, then poll `status --json` at 20 ms until the status
changes.

## Corollary

`applyMutation` must not predict what it cannot know. Starting a *queued* task
depends on the scheduler finding a free slot; a non-in-place restart mints an id
only the daemon knows; `add` likewise. All three return the state unchanged —
a wrong prediction is worse than none, because it flickers too.
