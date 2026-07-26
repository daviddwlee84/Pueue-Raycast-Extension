/**
 * The CLI implementation of `PueueTransport` — the only file that knows pueue
 * is a subprocess.
 *
 * Three CLI facts shape everything here:
 *
 *   1. Only `status`, `log`, and `group` emit JSON. Every mutation is
 *      exit-code plus prose, so `mutate` returns void (except `add`, which we
 *      coax into printing an id with `--print-task-id`).
 *   2. Global flags must precede the subcommand. `pueue status --color never`
 *      exits 2 with a clap error; `pueue --color never status` is correct.
 *   3. `status --json` embeds a full environment snapshot per task. It is
 *      dropped here, at the parse boundary, before anything can render it or
 *      write it to Raycast's disk-backed cache.
 *
 * Mutation argv lives in `argv.ts` rather than here, so it stays free of
 * Raycast imports and can be asserted against the real `--help` output.
 */

import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";

import { argvFor } from "./argv";
import {
  baseEnv,
  defaultConnection,
  resolvePueue,
  taskLogPath,
} from "./binary";
import { readsLocalLogs, type Connection } from "./connections";
import { sshArgv } from "./ssh";
import { fromExecError, PueueError } from "./errors";
import { readLogTail, type LogTail } from "./logfile";
import type {
  ConnectionOption,
  FollowHandlers,
  LogOptions,
  Mutation,
  PueueTransport,
  StatusOptions,
} from "./transport";
import type { GroupMap, LogMap, RawTask, State, Task } from "./types";

const pexecFile = promisify(execFile);

const READ_TIMEOUT_MS = 10_000;
const WRITE_TIMEOUT_MS = 15_000;
/** SSH adds a connection handshake, and BatchMode fails rather than prompting. */
const SSH_TIMEOUT_MS = 30_000;

/**
 * `status --json` carries a full env snapshot per task — upstream cites ~2 MB
 * for 400 tasks — and `log --full` is unbounded by design. execFile's default
 * 1 MB buffer would truncate both into a JSON parse error.
 */
const BIG_BUFFER = 64 * 1024 * 1024;

/**
 * Global options, which must come *before* the subcommand.
 *
 * `--color never` does nothing for stderr (that is color_eyre, which ignores
 * it) but does clean up the prose stdout of the mutation commands, which we
 * surface in toasts.
 *
 * `--config` is how a connection is selected: pueue's own client reads the
 * socket path, secret, and TLS material from there, which is why driving a
 * remote daemon needs no protocol code in this extension.
 */
function globalArgs(connection: Connection): string[] {
  const cfg = connection.configPath;
  return ["--color", "never", ...(cfg ? ["--config", cfg] : [])];
}

function connectionOf(o: ConnectionOption | undefined): Connection {
  return o?.connection ?? defaultConnection();
}

interface RunOptions {
  timeout?: number;
  maxBuffer?: number;
  signal?: AbortSignal;
  connection?: Connection;
}

/** Every subprocess call funnels through here so failures classify uniformly. */
async function run(args: string[], o: RunOptions = {}): Promise<string> {
  const connection = o.connection ?? defaultConnection();
  const bin = resolvePueue();
  const argv = [...globalArgs(connection), ...args];
  try {
    const { stdout } = await pexecFile(bin, argv, {
      timeout: o.timeout ?? READ_TIMEOUT_MS,
      maxBuffer: o.maxBuffer ?? 1024 * 1024,
      env: baseEnv(),
      signal: o.signal,
    });
    return stdout;
  } catch (e) {
    throw fromExecError(e, [bin, ...argv]);
  }
}

/**
 * Run a pueue command on a remote host over SSH.
 *
 * Used only for submission. pueue canonicalises a task's working directory on
 * whichever machine the *client* runs on, so submitting a remote job from here
 * fails in one of three ways — a local path that doesn't exist there, a remote
 * path the local client refuses to canonicalise, or macOS silently rewriting
 * /tmp to /private/tmp. Running the client on the far side removes the problem
 * entirely rather than working around it.
 *
 * Reads and control commands keep using the forwarded socket, which is faster
 * and needs no second authentication.
 */
async function runOverSsh(
  host: string,
  pueueArgs: readonly string[],
): Promise<string> {
  const argv = sshArgv(host, pueueArgs);
  try {
    const { stdout } = await pexecFile("ssh", argv, {
      timeout: SSH_TIMEOUT_MS,
      maxBuffer: 1024 * 1024,
      env: baseEnv(),
    });
    return stdout;
  } catch (e) {
    throw fromExecError(e, ["ssh", ...argv]);
  }
}

function parseJson<T>(stdout: string, what: string): T {
  try {
    return JSON.parse(stdout) as T;
  } catch {
    throw new PueueError("command-failed", `Could not parse ${what} as JSON`);
  }
}

/**
 * Drop `envs` before anything can cache it.
 *
 * `useCachedPromise` persists results to Raycast's disk-backed Cache, so
 * keeping this field would write every secret in the submitting shell's
 * environment to disk in plaintext. Nothing in the UI needs it; `taskEnvs()`
 * fetches it on demand when a user explicitly asks.
 */
function stripEnvs(tasks: Record<string, RawTask>): Record<string, Task> {
  const out: Record<string, Task> = {};
  for (const [id, task] of Object.entries(tasks ?? {})) {
    const lean: Partial<RawTask> = { ...task };
    delete lean.envs;
    out[id] = lean as Task;
  }
  return out;
}

export function createCliTransport(): PueueTransport {
  return {
    async readState(o: StatusOptions = {}): Promise<State> {
      const args = ["status", "--json"];
      if (o.group) args.push("--group", o.group);
      // <QUERY>... is variadic but the DSL is one expression — pass it whole.
      if (o.query?.trim()) args.push(o.query.trim());

      const stdout = await run(args, {
        maxBuffer: BIG_BUFFER,
        signal: o.signal,
        connection: connectionOf(o),
      });
      const raw = parseJson<{
        tasks: Record<string, RawTask>;
        groups: GroupMap;
      }>(stdout, "pueue status");
      // Guard against ever being handed `group --json`'s shape, which has the
      // group names at the top level and would silently yield zero tasks.
      if (!raw || typeof raw !== "object" || !("tasks" in raw)) {
        throw new PueueError(
          "command-failed",
          "Unexpected `pueue status --json` payload",
        );
      }
      return { tasks: stripEnvs(raw.tasks), groups: raw.groups ?? {} };
    },

    async readGroups(
      o: ConnectionOption & { signal?: AbortSignal } = {},
    ): Promise<GroupMap> {
      const stdout = await run(["group", "--json"], {
        signal: o.signal,
        connection: connectionOf(o),
      });
      const parsed = parseJson<GroupMap | { groups: GroupMap }>(
        stdout,
        "pueue group",
      );
      // `group --json` returns the inner map. If upstream ever aligns it with
      // `status --json`, unwrap rather than break.
      return (
        parsed && "groups" in parsed ? parsed.groups : parsed
      ) as GroupMap;
    },

    async readLogs(ids: number[], o: LogOptions = {}): Promise<LogMap> {
      if (ids.length === 0) return {};
      const args = ["log", ...ids.map(String), "--json"];
      if (o.full) args.push("--full");
      else if (o.lines !== undefined) args.push("--lines", String(o.lines));

      const stdout = await run(args, {
        maxBuffer: BIG_BUFFER,
        signal: o.signal,
        connection: connectionOf(o),
      });
      // An unknown id is `{}` with exit 0 — an empty result, not an error.
      return parseJson<LogMap>(stdout, "pueue log");
    },

    async mutate(m: Mutation, o?: ConnectionOption): Promise<number | void> {
      const connection = connectionOf(o);
      const stdout = connection.sshHost
        ? await runOverSsh(connection.sshHost, argvFor(m))
        : await run(argvFor(m), {
            timeout: WRITE_TIMEOUT_MS,
            connection,
          });
      if (m.op !== "add") return;
      // --print-task-id makes stdout just the integer.
      const id = Number.parseInt(stdout.trim(), 10);
      return Number.isNaN(id) ? undefined : id;
    },

    followLog(
      id: number,
      lines: number,
      h: FollowHandlers,
      o?: ConnectionOption,
    ): () => void {
      const connection = connectionOf(o);
      let bin: string;
      try {
        bin = resolvePueue();
      } catch (e) {
        h.onError(e as Error);
        return () => {};
      }

      const child = spawn(
        bin,
        [
          ...globalArgs(connection),
          "follow",
          String(id),
          "--lines",
          String(lines),
        ],
        {
          env: baseEnv(),
        },
      );
      let stderr = "";

      child.stdout?.setEncoding("utf8");
      child.stderr?.setEncoding("utf8");
      child.stdout?.on("data", (d: string) => h.onData(d));
      child.stderr?.on("data", (d: string) => (stderr += d));
      child.on("error", (e) => h.onError(e));
      child.on("close", (code) => {
        // `follow` exits by itself when the task stops — that is completion,
        // not failure. Only a non-zero code (bad id) is an error.
        if (code && code !== 0)
          h.onError(fromExecError({ code, stderr }, [bin, "follow"]));
        h.onDone(code);
      });

      return () => {
        if (!child.killed) child.kill("SIGTERM");
      };
    },

    async probe(o?: ConnectionOption) {
      const stdout = await run(["--version"], {
        timeout: 5_000,
        connection: connectionOf(o),
      });
      // Never infer capability from an exit code — parse the shape. `pueue`
      // prints "pueue 4.0.4".
      const m = /^pueue\s+(\d+)\.(\d+)/i.exec(stdout.trim());
      const version = stdout.trim().replace(/^pueue\s+/i, "") || "unknown";
      // Unparseable means a pueue we don't recognise; assume it speaks v4
      // rather than blocking the user on our own guess.
      const major = m ? Number(m[1]) : 4;
      return { version, major, reachable: true };
    },
  };
}

/* -- direct file access, outside the transport ---------------------------- */

/**
 * One task's environment snapshot, read raw.
 *
 * Deliberately bypasses the transport, because the transport's entire job for
 * this field is to throw it away before it can reach Raycast's disk-backed
 * cache. Nothing here is cached or retained: it re-reads, indexes one task, and
 * returns. Only call it when a user explicitly asks to see the environment.
 *
 * The query DSL cannot help narrow this — its filter columns are status,
 * command, label, start, end, and enqueue_at, with no `id` — so this pays for a
 * full state read.
 */
export async function readTaskEnvs(
  id: number,
  connection: Connection = defaultConnection(),
): Promise<Record<string, string> | undefined> {
  const stdout = await run(["status", "--json"], {
    maxBuffer: BIG_BUFFER,
    connection,
  });
  const raw = parseJson<{ tasks: Record<string, RawTask> }>(
    stdout,
    "pueue status",
  );
  return raw.tasks?.[String(id)]?.envs;
}

/**
 * Read a task's log straight off disk.
 *
 * Going direct skips the JSON string-escape round trip and lets us tail rather
 * than load a whole file. The tail logic itself lives in `logfile.ts` so it can
 * be asserted without Raycast.
 */
export async function readLogFromDisk(
  id: number,
  maxBytes = 512 * 1024,
  connection: Connection = defaultConnection(),
): Promise<LogTail | undefined> {
  // The guard that matters. A remote daemon's logs are not on this disk — but
  // the *local* log directory usually exists, so an unguarded read returns a
  // different task's output under the same id instead of failing. Silently
  // wrong beats loudly wrong only for the person who wrote the bug.
  if (!readsLocalLogs(connection)) return undefined;

  const path = taskLogPath(id);
  return path ? readLogTail(path, maxBytes) : undefined;
}
