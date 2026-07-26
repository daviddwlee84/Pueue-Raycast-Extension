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
} from "./errors";
export type { PueueErrorKind } from "./errors";
export {
  connectionByName,
  connections,
  defaultConnection,
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
 * A timestamped read.
 *
 * The menu bar keeps the data and its age in one cache entry so the two can
 * never drift — which matters because Raycast restores a menu bar item from its
 * database rather than by re-running the command, so a stale render can outlive
 * a restart. Showing *when* it was read turns that from misleading into merely
 * old.
 */
export async function snapshot(o?: StatusOptions): Promise<Snapshot> {
  return { state: await status(o), fetchedAt: Date.now() };
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
