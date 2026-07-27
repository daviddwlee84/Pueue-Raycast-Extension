/**
 * What the last submission used, remembered per connection.
 *
 * Both values are daemon-specific: a group that exists here may not exist there,
 * and a local path is meaningless on a remote host. Sharing one key would seed a
 * form with values from whichever machine you happened to use last.
 *
 * This module exists because the working directory has a sharp edge. `pueue add`
 * without `--working-directory` uses **the client's current directory**, and the
 * client here is a subprocess of Raycast — whose cwd is not your shell's, not
 * your project's, and measured to be `/`. A task queued that way runs `make` at
 * the filesystem root. Every submission path therefore passes a directory
 * explicitly rather than letting pueue fall back.
 *
 * The one exception is a connection that runs over ssh: there the pueue client
 * runs on the far side, in the login shell's directory, which is the remote
 * `$HOME` — a sensible default we could not compute locally anyway. See
 * `defaultWorkingDirectory`.
 */

import { LocalStorage } from "@raycast/api";
import { homedir } from "node:os";

import { runsOverSsh, type Connection } from "./pueue";

export const lastGroupKey = (connection: string) => `add.group:${connection}`;
export const lastCwdKey = (connection: string) => `add.cwd:${connection}`;

export async function readLastGroup(connection: string): Promise<string> {
  return (
    (await LocalStorage.getItem<string>(lastGroupKey(connection))) ?? "default"
  );
}

export async function readLastCwd(
  connection: string,
): Promise<string | undefined> {
  const stored = await LocalStorage.getItem<string>(lastCwdKey(connection));
  return stored && stored.length > 0 ? stored : undefined;
}

export async function rememberLastUsed(
  connection: string,
  o: { group?: string; cwd?: string },
): Promise<void> {
  if (o.group) await LocalStorage.setItem(lastGroupKey(connection), o.group);
  if (o.cwd) await LocalStorage.setItem(lastCwdKey(connection), o.cwd);
}

/**
 * Where a task should run when the caller did not say.
 *
 * `undefined` means "let pueue decide", and is only ever returned for an ssh
 * connection, where pueue's own decision is the remote home directory. For
 * anything the local client submits, a concrete path is always returned —
 * leaving it to pueue there means the Raycast process's cwd.
 */
export async function defaultWorkingDirectory(
  connection: Connection,
): Promise<string | undefined> {
  if (runsOverSsh(connection)) return undefined;
  return (await readLastCwd(connection.name)) ?? homedir();
}
