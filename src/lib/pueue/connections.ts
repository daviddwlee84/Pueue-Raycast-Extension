/**
 * Named pueue connections.
 *
 * A "connection" is just a client config — pueue's own client does all the
 * work, so driving a remote daemon needs no protocol code here at all. The
 * recommended setup is an SSH-forwarded unix socket with a client-only config
 * selected by `-c`; see `docs/remote.md`.
 *
 * Two things make a connection more than a path:
 *
 *   1. **Logs.** A remote client must not read task logs off *this* disk. The
 *      local log directory usually exists, so a naive read returns a different
 *      task's output under the same id rather than failing — silently wrong,
 *      which is the worst kind.
 *   2. **Submission.** pueue canonicalises a task's working directory on the
 *      *client*, so submitting a remote job from here either fails to spawn or
 *      is refused outright. An optional SSH host lets us submit on the remote
 *      box instead, where the paths actually exist.
 */

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export interface Connection {
  /** Display name. "Local" for the implicit default. */
  name: string;
  /** `--config` path. Undefined means pueue's own default config. */
  configPath?: string;
  /** When set, tasks are submitted with `ssh <host> 'pueue add …'`. */
  sshHost?: string;
  /** True for anything that isn't this machine's own daemon. */
  remote: boolean;
}

export const LOCAL_CONNECTION_NAME = "Local";

export function expandTilde(p: string): string {
  return p.replace(/^~(?=\/|$)/, homedir());
}

/**
 * Parse the `connections` preference.
 *
 * One connection per line, fields separated by `|`:
 *
 *     gpu-box | ~/.config/pueue/remote/client.yml | gpu.example.com
 *     laptop  | ~/.config/pueue/laptop/client.yml
 *
 * The third field is optional and is an SSH destination — anything `ssh`
 * accepts, including a `~/.ssh/config` host alias. Blank lines and `#` comments
 * are ignored so the field can be annotated.
 */
export function parseConnections(raw: string | undefined): Connection[] {
  const out: Connection[] = [];
  for (const line of (raw ?? "").split(/\r?\n/)) {
    const text = line.trim();
    if (!text || text.startsWith("#")) continue;

    const [name, configPath, sshHost] = text.split("|").map((f) => f.trim());
    if (!name || !configPath) continue;

    out.push({
      name,
      configPath: expandTilde(configPath),
      sshHost: sshHost || undefined,
      remote: true,
    });
  }
  return out;
}

/**
 * Whether pueue's client will serve logs from local disk for this connection.
 *
 * `client.read_local_logs` defaults to true. A remote client **must** set it
 * false — otherwise pueue itself tries to read the remote daemon's log
 * directory on the local filesystem. We read the same setting to decide whether
 * our own on-disk fast path is safe, which means we can never disagree with
 * pueue about where a log lives.
 *
 * Probed with a regex rather than a YAML parser: one boolean does not justify a
 * dependency, and an unreadable config just costs us the fast path.
 */
export function readsLocalLogs(connection: Connection): boolean {
  // No SSH host and no custom config: plainly this machine's own daemon.
  if (!connection.remote && !connection.configPath) return true;
  // A remote box's logs are never on this disk, whatever the config says.
  if (connection.remote && connection.sshHost) return false;

  const path =
    connection.configPath ??
    join(homedir(), "Library", "Application Support", "pueue", "pueue.yml");

  try {
    const yaml = readFileSync(path, "utf8");
    // `read_local_logs: false` — anything else, including absent, means true.
    return !/^\s*read_local_logs:\s*false\s*$/m.test(yaml);
  } catch {
    // Unreadable config. Assume the safe answer: don't touch local files.
    return !connection.remote;
  }
}
