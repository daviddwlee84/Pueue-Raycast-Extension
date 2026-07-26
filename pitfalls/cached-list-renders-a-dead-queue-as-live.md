# Tasks shown as Running after the daemon stopped

## Symptoms (grep this section)

```text
Failed to fetch latest data
Failed to initialize client.
```

The Tasks list keeps showing a plausible queue — tasks marked **Running**, with
durations — while `pueued` is not running at all. The only hint is a one-line
toast at the bottom of the window, which scrolls away.

## Cause

`useCachedPromise` keeps serving its last successful result when a fetch fails.
That is the right default for a flaky read; it is the wrong default when the
failure is *structural*, because then the cached data isn't a moment stale — it
is a snapshot of a queue nobody can see any more.

Found by actually running `brew services stop pueue` and looking, rather than by
reading the code: the list cheerfully rendered a `sleep 300` task as Running
minutes after the process had died.

## Fix

Structural failures — binary missing, daemon unreachable — get a row of their
own at the top of the list, carrying the same recovery actions as the
full-screen view:

```tsx
const stale = error !== undefined && describeError(error).structural;
…
{stale ? (
  <List.Section title="Connection">
    <StaleBannerItem error={error} onRetry={reload} />
  </List.Section>
) : null}
```

Transient failures still use the toast. The full-screen `ErrorEmptyView` is
reserved for a failed *first* read, when there is nothing to show beside it.

## Sizing note

The banner needs its own short title. With the detail pane open the list column
is about a third of the window, and the full titles truncated to nonsense:

```text
Pueu...  Showing...  Not...ected
```

Hence `ErrorDescriptor.shortTitle` — "Daemon not running", plus a `cached`
accessory, and no subtitle.

## Related

The same principle drives the menu bar's `Updated HH:MM` row. Raycast restores a
menu bar item from its database on restart rather than by re-running the
command, so a stale render can outlive a restart with nothing to indicate it.
Showing *when* the data was read turns an invisible problem into a visible one.
