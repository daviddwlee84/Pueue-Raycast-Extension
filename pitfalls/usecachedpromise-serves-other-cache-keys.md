# `useCachedPromise` serves data from a different cache key

## Symptoms (grep this section)

No error message. That is the problem.

You select a second connection — a remote host, another profile, any argument
that keys the cache — and the view renders **the previous connection's data**
under the new one's name. In this extension it looked like this: a menu bar
showing `nas` selected, with

```text
Groups
  default — running (0/1)
```

underneath, on a host that could not be reached at all. The `default` group
belonged to the local daemon. No toast, no red icon, no indication that the read
had failed, because the code's "did the read fail" branch was
`if (!data)` — and `data` was truthy.

## Cause

`useCachedPromise(fn, args, …)` keys its cache on `hash(args)` with a namespace
of `hash(fn)`. That much works: each connection gets its own entry.

`keepPreviousData: true` then defeats it. From
`node_modules/@raycast/utils/dist/main.js` (v2.2.7, around line 759):

```js
if (lastUpdateFrom.current === "promise") returnedData = laggyDataRef.current;
else if (keepPreviousData && cachedData !== emptyCache) returnedData = cachedData;
else if (keepPreviousData && cachedData === emptyCache)
  // if the cache is empty, we will return the previous data
  returnedData = laggyDataRef.current;
```

`laggyDataRef` is **key-independent** — it holds the last value any successful
run of this hook produced, regardless of arguments. And `lastUpdateFrom.current`
is set to `"promise"` on the first success and never reset. So after one good
read:

- args change to a connection with no cache entry
- the new promise rejects
- `returnedData` is still `laggyDataRef.current`, i.e. the *old* connection's data

The hook is behaving as documented — "keep the previous data while the new
request is in flight" is the whole feature. The trap is that "previous" means
previous *in time*, not previous *for this key*, and that it persists through a
failure rather than only through a pending state.

## Why keying the cache is not enough

The natural fix — "make the connection part of the arguments so the cache keys
differ" — was already in place when this bug shipped:

```ts
useCachedPromise(
  (connectionName: string) => readStatus({ connection: connectionByName(connectionName) }),
  [conn.connection.name],      // already keyed correctly
  { keepPreviousData: true },
)
```

Correct keying prevents the *cache* from mixing connections. It does not touch
`laggyDataRef`, which is a separate mechanism sitting in front of the cache.

## Fix

Stamp the payload with what it is, and check on the way out. The guard cannot
live in the hook's arguments, so it has to live in the data:

```ts
export interface Snapshot {
  state: State;
  fetchedAt: number;
  connection: string;   // the Connection.name this was read from
}

export function forConnection(snap: Snapshot | undefined, name: string) {
  return snap && snap.connection === name ? snap : undefined;
}
```

```ts
const snap = forConnection(state.data, conn.connection.name);
if (error && !snap) return <ErrorEmptyView error={error} … />;
```

`undefined` routes the caller into its existing no-data-plus-error branch, which
is exactly right: there genuinely is no data for this connection.

This gives up `keepPreviousData`'s no-blank-frame benefit *across* connections —
one read's worth of empty list with a spinner. That is the point. The frame it
used to fill was the one showing another machine's queue.

## Generalisation

Any hook that caches by key and also has a "keep showing something" fallback can
serve you a value from the wrong key. If the identity of the thing you fetched
matters — a host, a document, an account, a tenant — put it *in the value*, not
only in the key. A payload that can say who it belongs to makes the mistake
unrepresentable; an argument list can only make it unlikely.

## Related

The same failure mode as [a cached list rendering a dead queue as a live
one](cached-list-renders-a-dead-queue-as-live.md): cached data outliving the thing it
described. That one was solved by *labelling* the staleness, because the data
was still the right daemon's, only old. This one cannot be labelled — data from
the wrong machine is not stale, it is wrong — so it is rejected instead.
