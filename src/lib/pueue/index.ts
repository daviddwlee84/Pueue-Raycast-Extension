/**
 * The public API. Views import from here and nowhere else inside `pueue/`.
 *
 * Keeping the factory here rather than in `transport.ts` keeps that module
 * types-only, so `cli-transport.ts` can depend on it without an import cycle.
 */

import {
  createCliTransport,
  readLogFromDisk,
  readTaskEnvs,
} from "./cli-transport";
import { defaultConnection } from "./binary";
import { cleanLogOutput } from "./normalize";
import type { Connection } from "./connections";
import type {
  ConnectionOption,
  FollowHandlers,
  LogOptions,
  Mutation,
  PueueTransport,
  StatusOptions,
} from "./transport";
import type { GroupMap, LogMap, Snapshot, State } from "./types";

export * from "./types";
export * from "./normalize";
export * from "./connections";
export {
  PueueError,
  cleanStderr,
  firstLine,
  isBadQuery,
  isBinaryMissing,
  isDaemonDown,
  isHostUnreachable,
} from "./errors";
export type { PueueErrorKind } from "./errors";
export {
  connectionByName,
  connections,
  defaultConnection,
  invalidConnectionLines,
  isBrewManagedDaemon,
  pueueDirectory,
  resolveBrew,
  resolvePueue,
  resolvePueued,
  taskLogPath,
} from "./binary";
export type {
  FollowHandlers,
  LogOptions,
  Mutation,
  PueueTransport,
  StatusOptions,
} from "./transport";
export { readLogFromDisk } from "./cli-transport";

let active: PueueTransport | undefined;

/** The active transport. Swap point for `backlog/socket-transport.md`. */
export function transport(): PueueTransport {
  return (active ??= createCliTransport());
}

/** Testing / future-transport hook. */
export function setTransport(t: PueueTransport | undefined): void {
  active = t;
}

export const status = (o?: StatusOptions): Promise<State> =>
  transport().readState(o);
export const groups = (
  o?: ConnectionOption & { signal?: AbortSignal },
): Promise<GroupMap> => transport().readGroups(o);
export const logs = (ids: number[], o?: LogOptions): Promise<LogMap> =>
  transport().readLogs(ids, o);
export const mutate = (
  m: Mutation,
  o?: ConnectionOption,
): Promise<number | void> => transport().mutate(m, o);
export const follow = (
  id: number,
  lines: number,
  h: FollowHandlers,
  o?: ConnectionOption,
): (() => void) => transport().followLog(id, lines, h, o);
export const probe = (o?: ConnectionOption) => transport().probe(o);

/**
 * A read stamped with its age and its daemon.
 *
 * Both stamps exist to stop `useCachedPromise` lying by omission. Keeping the
 * data and its age in one cache entry means they cannot drift, which matters
 * because Raycast restores a menu bar item from its database rather than by
 * re-running the command — showing *when* it was read turns a stale render from
 * misleading into merely old.
 *
 * Keeping the *connection* in the same entry is the stronger guarantee: the
 * hook's `keepPreviousData` will happily hand a view the last successful read
 * from a different daemon when the selected one cannot be reached. Only the
 * payload itself can settle who it belongs to. See `Snapshot`.
 */
export async function snapshot(o?: StatusOptions): Promise<Snapshot> {
  const connection = o?.connection ?? defaultConnection();
  return {
    state: await status({ ...o, connection }),
    fetchedAt: Date.now(),
    connection: connection.name,
  };
}

/**
 * A snapshot, but only if it came from the daemon you asked about.
 *
 * The one guard every view needs. `undefined` means "nothing for this
 * connection yet", which is the honest answer while a switch is in flight or
 * after it has failed — and it routes the caller into its existing
 * no-data-plus-error branch rather than into someone else's queue.
 */
export function forConnection(
  snap: Snapshot | undefined,
  name: string,
): Snapshot | undefined {
  return snap && snap.connection === name ? snap : undefined;
}

/**
 * A task's captured output, preferring the on-disk file and falling back to
 * the CLI. Returns undefined when there is genuinely no output.
 *
 * `pueue log` reports a missing log file by putting its own error text in the
 * `output` field and exiting 0, so that has to be filtered out here rather than
 * caught — otherwise a task that never ran renders a Rust I/O error as if the
 * task had printed it.
 */
export async function readLogText(
  id: number,
  o: { lines?: number; full?: boolean; connection?: Connection } = {},
): Promise<{ text: string; truncated: boolean; path?: string } | undefined> {
  if (o.full) {
    // Only ever reads local files when the connection says that is where this
    // task's log actually lives. See readLogFromDisk.
    const disk = await readLogFromDisk(id, undefined, o.connection);
    if (disk) return disk;
  }
  const map = await logs(
    [id],
    o.full
      ? { full: true, connection: o.connection }
      : { lines: o.lines ?? 20, connection: o.connection },
  );
  const text = cleanLogOutput(map[String(id)]?.output);
  return text === undefined ? undefined : { text, truncated: !o.full };
}

/**
 * A task's environment snapshot, on demand.
 *
 * Uncached and never called from a render path: this is the field the transport
 * strips from every parsed task precisely so it cannot reach Raycast's
 * disk-backed cache. Only fetch it when a user explicitly asks to see it.
 */
export async function taskEnvs(
  id: number,
  connection?: Connection,
): Promise<Record<string, string>> {
  return (await readTaskEnvs(id, connection)) ?? {};
}
