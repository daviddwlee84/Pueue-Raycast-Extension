/**
 * Turning pueue's failures into something a Raycast user can act on.
 *
 * pueue has no structured error output at all: every failure is human prose on
 * stderr, and the exit code is only ever 0, 1, or 2. Worse, `color_eyre` writes
 * SGR escapes to stderr *even when stderr is a pipe* — neither `--color never`
 * (a global flag; putting it after the subcommand is itself an exit-2 clap
 * error) nor `NO_COLOR=1` suppresses them. All verified against v4.0.4.
 *
 * So: strip, classify, and render. `fixtures/stderr.json` holds all seven
 * failure shapes captured byte-exact from the real binary, and `dev-check.ts`
 * asserts this module against every one of them.
 */

export type PueueErrorKind =
  /** `resolvePueue()` found nothing, or the spawn returned ENOENT. */
  | "binary-not-found"
  /** The config file itself is missing or unreadable. */
  | "config-missing"
  /** pueue is installed but pueued isn't reachable — never started, or stopped. */
  | "daemon-not-running"
  /** The status query DSL didn't parse. User error, and the diagnostic is good. */
  | "bad-query"
  /** clap rejected our argv. Always an extension bug — surface it verbatim. */
  | "bad-arguments"
  | "timeout"
  | "command-failed";

export class PueueError extends Error {
  readonly kind: PueueErrorKind;
  /** ANSI-stripped, eyre-noise-stripped, ready to render. */
  readonly detail: string;
  readonly exitCode: number | null;
  readonly argv: readonly string[];

  constructor(
    kind: PueueErrorKind,
    detail: string,
    opts: { exitCode?: number | null; argv?: readonly string[] } = {},
  ) {
    super(firstLine(detail) || kind);
    this.name = "PueueError";
    this.kind = kind;
    this.detail = detail;
    this.exitCode = opts.exitCode ?? null;
    this.argv = opts.argv ?? [];
  }
}

export const isBinaryMissing = (e: unknown): boolean =>
  e instanceof PueueError && e.kind === "binary-not-found";

/**
 * True for both "pueued has never run" and "the socket is unreachable". They
 * produce different stderr but the remedy is identical, so the UI treats them
 * as one state and shows the raw detail to distinguish them.
 */
export const isDaemonDown = (e: unknown): boolean =>
  e instanceof PueueError &&
  (e.kind === "daemon-not-running" || e.kind === "config-missing");

export const isBadQuery = (e: unknown): boolean =>
  e instanceof PueueError && e.kind === "bad-query";

// eslint-disable-next-line no-control-regex -- color_eyre emits raw SGR even when stderr is a pipe
const ANSI_SGR = /\u001b\[[0-9;]*m/g;

/**
 * Reduce a color_eyre report to the part that means something.
 *
 * Everything after `Location:` is a Rust source path and two RUST_BACKTRACE
 * hints — noise for anyone who isn't debugging pueue itself. The `   0: ` /
 * `   1: ` prefixes are eyre's cause-chain numbering; stripping them turns the
 * chain into plain lines. The pattern requires a colon, so pest's diagnostic
 * gutter (`      1 | status=Nope`, a pipe) survives intact — that alignment is
 * the whole value of a bad-query message.
 */
export function cleanStderr(raw: string): string {
  const kept: string[] = [];
  for (const line of raw.replace(ANSI_SGR, "").split("\n")) {
    if (/^Location:\s*$/.test(line)) break;
    if (/^Backtrace omitted/.test(line)) break;
    if (/^Run with RUST_BACKTRACE/.test(line)) continue;
    kept.push(line);
  }
  return kept
    .join("\n")
    .replace(/^Error:[ \t]*\r?\n?/, "")
    .replace(/^[ \t]*\d+:[ \t]+/gm, "")
    .replace(/^Pueue:[ \t]*/gm, "")
    .trim();
}

/** The first non-empty line — what a toast title or an Error.message gets. */
export function firstLine(detail: string): string {
  return (
    detail
      .split("\n")
      .find((l) => l.trim().length > 0)
      ?.trim() ?? ""
  );
}

/**
 * Bucket a cleaned message. Ordered most-specific first.
 *
 * The strings matched here were captured from pueue 4.0.4, not guessed:
 *
 *   config-missing      "Failed to read configuration." + "while opening config file"
 *   daemon-not-running  "while opening secret file. Did you start the daemon at least once?"
 *                       "while connecting to daemon. Did you start it?"  (ENOENT or ECONNREFUSED)
 *   bad-query           "Failed to parse query" + a pest diagnostic
 *   bad-arguments       clap, exit 2, "error: unexpected argument '--color' found"
 */
export function classify(
  detail: string,
  exitCode: number | null,
): PueueErrorKind {
  if (/Failed to parse query/i.test(detail)) return "bad-query";
  if (exitCode === 2 && /^error:/im.test(detail)) return "bad-arguments";

  // Check the daemon cases before the config case: a missing secret file is
  // reported as an I/O error on a path, and so is a missing config file.
  if (/while connecting to daemon/i.test(detail)) return "daemon-not-running";
  if (/while opening secret file/i.test(detail)) return "daemon-not-running";
  if (/Did you start the daemon/i.test(detail)) return "daemon-not-running";
  if (/Did you start it\?/i.test(detail)) return "daemon-not-running";

  if (/Couldn't find a configuration file/i.test(detail))
    return "config-missing";
  if (/Failed to read configuration/i.test(detail)) return "config-missing";
  if (/while opening config file/i.test(detail)) return "config-missing";

  return "command-failed";
}

/**
 * Build a PueueError from a rejected child process.
 *
 * Node reports a missing executable as ENOENT on the spawn itself, which is a
 * different thing from pueue running and failing — and it is the single most
 * likely failure in a Raycast extension, because Raycast runs under launchd
 * with no shell rc and never has Homebrew on PATH.
 */
export function fromExecError(e: unknown, argv: readonly string[]): PueueError {
  if (e instanceof PueueError) return e;

  const err = e as {
    code?: string | number;
    killed?: boolean;
    signal?: string;
    stderr?: string;
  };
  const detail = cleanStderr(String(err.stderr ?? ""));

  if (err.code === "ENOENT") {
    return new PueueError("binary-not-found", "pueue CLI not found", { argv });
  }
  // execFile reports its own timeout as a SIGTERM kill, not as a distinct code.
  if (err.killed && (err.signal === "SIGTERM" || err.code === "ETIMEDOUT")) {
    return new PueueError("timeout", detail || "pueue timed out", { argv });
  }

  const exitCode = typeof err.code === "number" ? err.code : null;
  const message = detail || (e instanceof Error ? e.message : String(e));
  return new PueueError(classify(message, exitCode), message, {
    exitCode,
    argv,
  });
}
