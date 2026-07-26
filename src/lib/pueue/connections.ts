/**
 * Named pueue connections.
 *
 * A connection is either
 *
 *   - **ssh** — every command runs as `ssh <host> 'pueue …'`. Nothing to set up
 *     on either machine beyond pueue being installed and SSH working. This is
 *     the default because it is also fast: SSH connection multiplexing brings
 *     the per-call cost to 10–30 ms, against 22–44 ms for a *local* pueue.
 *     Without multiplexing it would be 200–400 ms, which is why `ssh.ts`
 *     insists on it.
 *
 *   - **socket** — a client-only config selected with `-c`, talking to an
 *     SSH-forwarded unix socket. Marginally faster still, but needs a tunnel
 *     kept alive, the daemon's shared secret copied over, and a config written.
 *     Worth it only if you are already maintaining the tunnel.
 *
 * The distinction that matters for correctness is the same either way: a remote
 * daemon's logs are not on this disk, and a task's working directory is
 * resolved wherever the *client* runs. SSH mode gets the second one right for
 * free, because the client runs on the remote box.
 *
 * Deliberately free of Raycast imports so the parsing can be asserted by
 * `just verify`; the preference-backed lookups live in `binary.ts`.
 */

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export type ConnectionMode = "local" | "ssh" | "socket";

export interface Connection {
  /** Display name. "Local" for the implicit default. */
  name: string;
  mode: ConnectionMode;
  /** `--config` path, for socket mode. Undefined means pueue's own default. */
  configPath?: string;
  /** SSH destination — anything `ssh` accepts, including a ~/.ssh/config alias. */
  sshHost?: string;
  /**
   * Path to `pueue` on the remote host.
   *
   * Needed more often than you'd hope: `ssh host 'cmd'` runs a *non-interactive*
   * shell, which reads no rc file, so a cargo install in ~/.cargo/bin is
   * invisible even though `which pueue` works when you log in.
   */
  remoteBinary?: string;
  /** True for anything that isn't this machine's own daemon. */
  remote: boolean;
}

export const LOCAL_CONNECTION_NAME = "Local";

export function expandTilde(p: string): string {
  return p.replace(/^~(?=\/|$)/, homedir());
}

/** A config path, as opposed to an SSH host. Hosts don't contain slashes. */
function looksLikePath(field: string): boolean {
  return field.includes("/") || /\.(ya?ml)$/i.test(field);
}

/**
 * Parse the `connections` preference.
 *
 * Connections are separated by `;` **or** a newline; fields within one by `|`.
 *
 * The semicolon is not a stylistic choice. Raycast has no multi-line preference
 * type — the union is appPicker | checkbox | dropdown | password | textfield |
 * file | directory, and `ray lint` rejects anything else — so a `textfield` is
 * the only option and there is no way to type a newline into it. Newlines are
 * still accepted for anyone editing the stored value by other means.
 *
 *     local_ubuntu                                 ssh, host = the name itself
 *     lab | gpu.example.com                        ssh
 *     lab | gpu.example.com | ~/.cargo/bin/pueue   ssh, explicit remote binary
 *     lab | ~/pueue/client.yml                     socket
 *     lab | ~/pueue/client.yml | gpu-host          socket for reads, ssh to submit
 *     lab | host1 ; gpu | host2                    two connections, one line
 *
 * The bare form is the point: the simplest thing a person can type — the SSH
 * host they already have in `~/.ssh/config` — is also a complete, working
 * configuration.
 *
 * Field two is told apart by whether it looks like a path, and field three
 * follows from that: after a config it is an SSH host, after a host it is the
 * remote pueue binary. Both are unambiguous, because a host never contains a
 * slash and a path always does.
 *
 * Blank lines and `#` comments are ignored so the field can be annotated.
 * Unparseable lines are returned as `invalid` rather than dropped, because
 * silently ignoring a line someone typed is indistinguishable from a bug.
 */
export function parseConnections(raw: string | undefined): {
  connections: Connection[];
  invalid: string[];
} {
  const connections: Connection[] = [];
  const invalid: string[] = [];

  // Split on ';' as well as newlines — see the docstring: a Raycast
  // preference is single-line, so ';' is the only usable separator in the UI.
  for (const line of (raw ?? "").split(/[\n;]/)) {
    const text = line.trim();
    if (!text || text.startsWith("#")) continue;

    const fields = text.split("|").map((f) => f.trim());
    const [name, second, third] = fields;

    if (!name || fields.length > 3) {
      invalid.push(text);
      continue;
    }

    // Bare name: an SSH host that is also its own label.
    if (fields.length === 1) {
      connections.push({ name, mode: "ssh", sshHost: name, remote: true });
      continue;
    }

    if (!second) {
      invalid.push(text);
      continue;
    }

    if (looksLikePath(second)) {
      // A config path — socket mode. Any third field is an SSH host for submits.
      connections.push({
        name,
        mode: "socket",
        configPath: expandTilde(second),
        sshHost: third || undefined,
        remote: true,
      });
    } else if (third && !looksLikePath(third)) {
      // Host plus a non-path third field means nothing. Say so rather than guess.
      invalid.push(text);
    } else {
      connections.push({
        name,
        mode: "ssh",
        sshHost: second,
        // NOT expandTilde'd: this path lives on the *remote* filesystem, and
        // expanding it against our home directory would be actively wrong.
        remoteBinary: third || undefined,
        remote: true,
      });
    }
  }

  return { connections, invalid };
}

/**
 * Whether pueue's client will serve logs from local disk for this connection.
 *
 * `client.read_local_logs` defaults to true. A remote *socket* client must set
 * it false, or pueue itself tries to read the remote daemon's log directory on
 * the local filesystem. We read the same setting, so we can never disagree with
 * pueue about where a log lives.
 *
 * The local directory usually exists, so getting this wrong doesn't fail — it
 * returns a different task's output under the same id.
 */
export function readsLocalLogs(connection: Connection): boolean {
  // SSH mode runs the client on the far side; its "local" disk is not ours.
  if (connection.mode === "ssh") return false;
  if (connection.mode === "local" && !connection.configPath) return true;

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

/** Whether every command for this connection runs through ssh. */
export function runsOverSsh(connection: Connection): boolean {
  return connection.mode === "ssh" && connection.sshHost !== undefined;
}

/**
 * Whether *submitting* goes over ssh.
 *
 * True for ssh mode, and also for a socket connection that names a host — the
 * socket is fine for reads but cannot fix the working-directory problem.
 */
export function submitsOverSsh(connection: Connection): boolean {
  return connection.sshHost !== undefined;
}
