/**
 * Which daemon a tool is talking about.
 *
 * Split out of `ai-shape.ts` because reading the preference needs `@raycast/api`,
 * and that would make the projection module unrunnable outside Raycast — and so
 * unassertable by `just verify`. One import boundary, one file.
 *
 * The rule here is the whole point: **an unknown connection name is an error.**
 * The UI's `connectionByName` falls back to Local, which is right for a dropdown
 * whose remembered value has gone stale, and wrong for a sentence. Task ids are
 * per-daemon, so "kill everything on lab" landing on this machine is not a near
 * miss — it is a different queue, and the tasks it stops are someone else's.
 */

import { connectionByName, connections, type Connection } from "./pueue";

export function resolveConnectionStrict(name: string | undefined): Connection {
  if (!name) return connectionByName(undefined);
  const all = connections();
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
export function connectionNames(): string[] {
  return connections().map((c) => c.name);
}
