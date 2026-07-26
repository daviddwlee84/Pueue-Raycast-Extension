# Direct CBOR unix-socket transport

**Status:** `P?` · **Effort:** `[L]` · not started

## What

Implement `PueueTransport` against pueued's socket instead of spawning `pueue`.
The interface exists for exactly this: `src/lib/pueue/cli-transport.ts` is the
only file that would be replaced, and the factory lives in
`src/lib/pueue/index.ts`.

The seam only works because mutations are modelled as a `Mutation` data union
rather than argv. A socket transport maps each variant to a `Request` directly;
if `mutate()` took `string[]` it would have to parse argv back into intent.

## The protocol

From `pueue_lib/src/network/`:

- **Transport** — a unix socket at `<pueue_dir>/pueue_<user>.socket`, or TLS TCP
  on `127.0.0.1:6924`. macOS default is the socket, at
  `~/Library/Application Support/pueue/pueue_<user>.socket`.
- **Auth** — send the contents of `<pueue_dir>/shared_secret` (512 bytes, mode
  0640) as the first message. **The reply is the daemon's version string**,
  which would give us a version check for free.
- **Framing** — an 8-byte big-endian `u64` length header, then the payload
  written in 1280-byte chunks.
- **Serialisation** — CBOR via `ciborium`. Not JSON.
- **Requests** — `Request::Status` is a unit variant, so it encodes as the bare
  CBOR string `"Status"`. `Response::Status(Box<State>)` comes back.

## Why it's worth considering

- Removes ~28 ms of process spawn per read. At a 1-minute menu bar interval that
  is irrelevant; for a list that revalidates on every mutation and selection
  change it is not nothing.
- `Request::Stream` is a genuine **push** channel for log following, replacing
  the `pueue follow` subprocess.
- Version detection comes free from the auth handshake, instead of parsing
  `pueue --version`.

## Why it isn't done

- It reimplements `pueue_lib`'s wire protocol in TypeScript and re-pins it on
  every pueue release. Upstream is explicit that the protocol is **not**
  backwards compatible — 4.0.0 both switched CBOR libraries and changed the
  message representation.
- The CLI path is already 22–44 ms and correct. This trades a stable, documented
  interface for an unstable, undocumented one to save tens of milliseconds.
- `Request::Stream` would only replace `pueue follow`, which already works and
  is verified to stream.

## Prior art

[`beeequeue/pueue-ui`](https://github.com/beeequeue/pueue-ui) has a working Node
implementation using `cbor-x` — `server/lib/pueued.ts`. Worth reading before
starting. Caveats: it is TCP/Windows-centric (`node:tls`,
`rejectUnauthorized: false`, `LOCALAPPDATA`), and its hand-written types omit
`Locked` and model `result` as only `"Success" | {Failed: number}` — missing
`FailedToSpawn`, `Killed`, `Errored`, and `DependencyFailed`. Ours are complete;
don't copy theirs.

## What would have to be true

Someone runs into the spawn latency as an actual problem, or wants push-based
state (not just logs). Neither is true today.
