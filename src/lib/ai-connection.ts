/**
 * Which daemon a tool is talking about.
 *
 * Split out of `ai-shape.ts` because reading the preference needs `@raycast/api`,
 * and that would make the projection module unrunnable outside Raycast — and so
 * unassertable by `just verify`. One import boundary, one file.
 *
 * Two rules live here.
 *
 * **An unknown connection name is an error.** The UI's `connectionByName` falls
 * back to Local, which is right for a dropdown whose remembered value has gone
 * stale, and wrong for a sentence. Task ids are per-daemon, so "kill everything
 * on lab" landing on this machine is not a near miss — it is a different queue,
 * and the tasks it stops are someone else's.
 *
 * **Preferences do not reach a tool.** Raycast passes extension-level
 * preferences to *commands*; a tool sees an empty preference object, so
 * `connections` comes back undefined and every remote disappears. Observed: the
 * menu bar listing `lab` and `nas` while a tool in the same session reported
 * only `Local`. The commands mirror the raw preference into `LocalStorage`
 * (see `AI_CONNECTIONS_KEY`) and this module falls back to it.
 */

import { LocalStorage } from "@raycast/api";

import {
  AI_CONNECTIONS_KEY,
  connectionByName,
  connections,
  LOCAL_CONNECTION_NAME,
  parseConnections,
  type Connection,
} from "./pueue";

/**
 * Every connection a tool can reach.
 *
 * Prefers the real preference, so nothing changes if Raycast ever starts
 * passing them through; falls back to the mirror the commands maintain.
 */
export async function toolConnections(): Promise<Connection[]> {
  const fromPreferences = connections();
  // More than the implicit local one means preferences were visible.
  if (fromPreferences.length > 1) return fromPreferences;

  const raw = await LocalStorage.getItem<string>(AI_CONNECTIONS_KEY);
  if (!raw) return fromPreferences;
  return [fromPreferences[0], ...parseConnections(raw).connections];
}

export async function resolveConnectionStrict(
  name: string | undefined,
): Promise<Connection> {
  const all = await toolConnections();
  if (!name) return all[0] ?? connectionByName(undefined);

  const found = all.find((c) => c.name.toLowerCase() === name.toLowerCase());
  if (found) return found;

  // Naming the alternatives lets the model correct itself in one turn instead
  // of guessing again.
  throw new Error(
    `No connection named "${name}". Configured connections: ${all
      .map((c) => c.name)
      .join(", ")}. Omit the connection to use this machine's own daemon.`,
  );
}

/** The connections a tool can be pointed at, for describing the choice. */
export async function connectionNames(): Promise<string[]> {
  return (await toolConnections()).map((c) => c.name);
}

/**
 * Said when a tool can see no remotes at all.
 *
 * "You have no remote connections" and "I cannot see your remote connections"
 * are different claims, and only one of them is safe to make. The mirror is
 * written whenever a command runs, so opening any Pueue command once is the
 * fix — and if there genuinely are none, this still reads correctly.
 */
export function noRemotesNote(names: string[]): string | undefined {
  if (names.length > 1) return undefined;
  return (
    `Only ${LOCAL_CONNECTION_NAME} is visible. Either no remote connections are ` +
    "configured, or none have been mirrored yet — Raycast does not pass " +
    "extension preferences to AI tools, so tools read a copy the commands " +
    "write. Opening any Pueue command once refreshes it."
  );
}
