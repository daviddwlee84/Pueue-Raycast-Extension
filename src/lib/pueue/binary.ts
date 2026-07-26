/**
 * Locating pueue, and the files it keeps.
 *
 * This is the #1 hazard in a Raycast extension: Raycast runs extensions in a
 * managed Node process under launchd, which never sources a shell rc, so
 * neither Homebrew nor ~/.cargo/bin is on PATH and a bare `pueue` fails with
 * `spawn pueue ENOENT`. Every call therefore goes through an absolute path.
 *
 * A terminal hides this completely — `npm run dev`'s console inherits your
 * full PATH — so anything touched here must be tested *from Raycast*.
 */

import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { getPreferenceValues } from "@raycast/api";

import {
  expandTilde,
  LOCAL_CONNECTION_NAME,
  parseConnections,
  type Connection,
} from "./connections";
import { PueueError } from "./errors";

/**
 * Ordered by likelihood, not alphabetically.
 *
 * Both Homebrew prefixes are probed: /opt/homebrew is Apple Silicon, but an
 * x86_64 Homebrew (including under Rosetta) installs to /usr/local, and
 * hardcoding either one breaks half of all Macs.
 */
const PROBE_DIRS = [
  "/opt/homebrew/bin",
  "/usr/local/bin",
  join(homedir(), ".cargo", "bin"),
  join(homedir(), ".local", "bin"),
  "/usr/bin",
  "/bin",
];

/** macOS puts config, data, socket, and logs all in this one directory. */
const MACOS_PUEUE_DIR = join(
  homedir(),
  "Library",
  "Application Support",
  "pueue",
);

let cachedPueue: string | undefined;
let cachedPueued: string | undefined;

function prefs(): Preferences {
  return getPreferenceValues<Preferences>();
}

/**
 * Absolute path to the `pueue` client.
 *
 * The preference is consulted *before* the module cache so that changing it
 * takes effect immediately rather than after the next command launch, and it is
 * existsSync-validated so a stale path degrades into the probe instead of
 * producing an unexplained ENOENT.
 */
export function resolvePueue(): string {
  const configured = prefs().pueuePath?.trim();
  if (configured && existsSync(configured)) return configured;

  if (cachedPueue && existsSync(cachedPueue)) return cachedPueue;

  for (const dir of PROBE_DIRS) {
    const candidate = join(dir, "pueue");
    if (existsSync(candidate)) {
      cachedPueue = candidate;
      return candidate;
    }
  }
  throw new PueueError("binary-not-found", "pueue CLI not found");
}

/**
 * Absolute path to the `pueued` daemon, or undefined.
 *
 * Prefers the sibling of the resolved client: a Homebrew client driving a
 * cargo-built daemon is version skew waiting to happen, and pueue v4 broke the
 * wire protocol outright.
 */
export function resolvePueued(): string | undefined {
  if (cachedPueued && existsSync(cachedPueued)) return cachedPueued;

  try {
    const sibling = join(dirname(resolvePueue()), "pueued");
    if (existsSync(sibling)) return (cachedPueued = sibling);
  } catch {
    // No client either — fall through to the plain probe.
  }
  for (const dir of PROBE_DIRS) {
    const candidate = join(dir, "pueued");
    if (existsSync(candidate)) return (cachedPueued = candidate);
  }
  return undefined;
}

/**
 * Environment for every spawn.
 *
 * HOME is passed through so pueue can find its config directory. NO_COLOR is
 * deliberately *not* set: it has no effect on color_eyre's stderr (verified),
 * and setting it would imply otherwise to the next reader.
 */
export function baseEnv(): NodeJS.ProcessEnv {
  return { ...process.env, HOME: process.env.HOME ?? homedir() };
}

/** The `--config` path for the local connection, if the user pinned one. */
export function configPath(): string | undefined {
  const p = prefs().configPath?.trim();
  return p ? expandTilde(p) : undefined;
}

/**
 * Every connection, the implicit local one first.
 *
 * The local entry still honours the `configPath` preference — someone may keep
 * a non-default local config — but is never treated as remote.
 */
export function connections(): Connection[] {
  return [
    {
      name: LOCAL_CONNECTION_NAME,
      mode: "local",
      configPath: configPath(),
      remote: false,
    },
    ...parseConnections(prefs().connections).connections,
  ];
}

/**
 * Lines of the `connections` preference we could not parse.
 *
 * Surfaced in the UI rather than swallowed: a typed line that produces nothing
 * and says nothing is indistinguishable from the feature being broken.
 */
export function invalidConnectionLines(): string[] {
  return parseConnections(prefs().connections).invalid;
}

export function defaultConnection(): Connection {
  return connections()[0];
}

/** Look a connection up by name, falling back to local rather than throwing. */
export function connectionByName(name: string | undefined): Connection {
  const all = connections();
  return all.find((c) => c.name === name) ?? all[0];
}

/**
 * The directory pueue keeps state and logs in.
 *
 * `pueue_directory` in the config wins if it is set to something other than
 * null; otherwise macOS's data dir, which is the same path as the config dir.
 * Probed with a regex rather than a YAML parser — one optional key does not
 * justify a dependency, and a miss only costs us the on-disk log fast path.
 */
export function pueueDirectory(): string | undefined {
  const cfg = configPath() ?? join(MACOS_PUEUE_DIR, "pueue.yml");
  try {
    const yaml = readFileSync(cfg, "utf8");
    const m =
      /^\s*pueue_directory:\s*(?!null\s*$)(?:"([^"]+)"|'([^']+)'|(\S+))\s*$/m.exec(
        yaml,
      );
    const dir = m?.[1] ?? m?.[2] ?? m?.[3];
    if (dir) return dir.replace(/^~(?=\/|$)/, homedir());
  } catch {
    // No readable config — fall back to the platform default below.
  }
  return existsSync(MACOS_PUEUE_DIR) ? MACOS_PUEUE_DIR : undefined;
}

/**
 * Where a task's captured output lives on disk.
 *
 * stdout and stderr are written to this one file, interleaved, uncompressed,
 * with no timestamps or stream markers. Reading it directly is what `pueue log`
 * itself does when `client.read_local_logs` is true (the default).
 */
export function taskLogPath(id: number): string | undefined {
  const dir = pueueDirectory();
  return dir ? join(dir, "task_logs", `${id}.log`) : undefined;
}

export function resolveBrew(): string | undefined {
  for (const candidate of ["/opt/homebrew/bin/brew", "/usr/local/bin/brew"]) {
    if (existsSync(candidate)) return candidate;
  }
  return undefined;
}

/**
 * True when pueued is managed by `brew services` on this machine.
 *
 * Gates the one-click "Start Daemon" action. Starting `pueued -d` from Raycast
 * would make the daemon a child of Raycast's launchd process, so every task it
 * later ran would inherit Raycast's minimal environment — the same PATH problem
 * as above, except it silently poisons every future task instead of failing
 * loudly once. Only offer the button when we can hand the job to launchd.
 */
export function isBrewManagedDaemon(): boolean {
  return (
    resolveBrew() !== undefined &&
    existsSync(
      join(homedir(), "Library", "LaunchAgents", "homebrew.mxcl.pueue.plist"),
    )
  );
}

/** Reset the probe caches. Only used by tests and preference changes. */
export function clearBinaryCache(): void {
  cachedPueue = undefined;
  cachedPueued = undefined;
}
