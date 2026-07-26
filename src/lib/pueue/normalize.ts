/**
 * Pure flatteners over the wire types. No I/O, no React — everything here is
 * unit-testable and is exercised by `dev-check.ts` against `fixtures/state.json`.
 *
 * The whole point of this module is that no view should ever have to know that
 * `status` is a two-level externally-tagged enum.
 */

import type { Task, TaskResult, TaskStatus } from "./types";

export type StatusKind =
  | "stashed"
  | "queued"
  | "running"
  | "paused"
  | "locked"
  | "done"
  /** A pueue version newer than we know about. Degrade, never throw. */
  | "unknown";

export type ResultKind =
  | "success"
  | "failed"
  | "failed-to-spawn"
  | "killed"
  | "errored"
  | "dependency-failed"
  | "unknown";

const STATUS_KINDS: Record<string, StatusKind> = {
  Stashed: "stashed",
  Queued: "queued",
  Running: "running",
  Paused: "paused",
  Locked: "locked",
  Done: "done",
};

/** The outer variant tag. `Locked` reports as "locked" — see `underlyingKind`. */
export function statusKind(s: TaskStatus): StatusKind {
  const tag = Object.keys(s)[0];
  return STATUS_KINDS[tag] ?? "unknown";
}

/**
 * `Locked` wraps the status the task will return to once editing finishes, and
 * the wrapping is recursive in the type. Peel it off.
 */
export function unwrapLocked(s: TaskStatus): TaskStatus {
  return "Locked" in s ? unwrapLocked(s.Locked.previous_status) : s;
}

/** What the task actually *is*, ignoring an edit lock. This is the one to filter on. */
export function underlyingKind(s: TaskStatus): StatusKind {
  return statusKind(unwrapLocked(s));
}

export function isLocked(s: TaskStatus): boolean {
  return "Locked" in s;
}

/** The `TaskResult`, or undefined when the task hasn't finished. */
export function taskResult(s: TaskStatus): TaskResult | undefined {
  const u = unwrapLocked(s);
  return "Done" in u ? u.Done.result : undefined;
}

export function resultKind(r: TaskResult | undefined): ResultKind | undefined {
  if (r === undefined) return undefined;
  if (typeof r === "string") {
    switch (r) {
      case "Success":
        return "success";
      case "Killed":
        return "killed";
      case "Errored":
        return "errored";
      case "DependencyFailed":
        return "dependency-failed";
      default:
        return "unknown";
    }
  }
  if ("Failed" in r) return "failed";
  if ("FailedToSpawn" in r) return "failed-to-spawn";
  return "unknown";
}

/** The process exit code. Only `{ Failed: n }` carries one — Success has none on the wire. */
export function exitCode(r: TaskResult | undefined): number | undefined {
  return r && typeof r === "object" && "Failed" in r ? r.Failed : undefined;
}

/** The OS error from `{ FailedToSpawn: "..." }`, which is usually the real diagnosis. */
export function spawnError(r: TaskResult | undefined): string | undefined {
  return r && typeof r === "object" && "FailedToSpawn" in r
    ? r.FailedToSpawn
    : undefined;
}

export const isDone = (t: Task): boolean => underlyingKind(t.status) === "done";
export const isRunning = (t: Task): boolean =>
  underlyingKind(t.status) === "running";
export const isPaused = (t: Task): boolean =>
  underlyingKind(t.status) === "paused";
export const isQueued = (t: Task): boolean =>
  underlyingKind(t.status) === "queued";
export const isStashed = (t: Task): boolean =>
  underlyingKind(t.status) === "stashed";
export const isSuccess = (t: Task): boolean =>
  resultKind(taskResult(t.status)) === "success";

/**
 * Anything that finished as something other than Success.
 *
 * Deliberately an allowlist: `Killed`, `Errored`, `FailedToSpawn` and
 * `DependencyFailed` are all failures, and a denylist would miss whichever
 * variant pueue adds next.
 */
export function isFailed(t: Task): boolean {
  const k = resultKind(taskResult(t.status));
  return k !== undefined && k !== "success";
}

/** Still owed by the queue: not finished and not sitting in the stash. */
export function isActive(t: Task): boolean {
  const k = underlyingKind(t.status);
  return k !== "done" && k !== "stashed";
}

/* -- timestamps ---------------------------------------------------------- */

/**
 * Guarded date parse.
 *
 * `new Date(null)` returns 1970-01-01 rather than `Invalid Date`, and
 * `Stashed.enqueue_at` is legitimately null — so an unguarded parse renders a
 * plain stashed task as "1970". Chrono emits RFC 3339 with microseconds and a
 * numeric offset ("2026-04-27T11:01:06.893055+08:00"); V8 accepts the extra
 * fractional digits and truncates to milliseconds.
 */
export function parseTs(s: string | null | undefined): Date | undefined {
  if (!s) return undefined;
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? undefined : d;
}

/**
 * When the task entered (or is scheduled to enter) the queue.
 *
 * Handles the `enqueue_at` / `enqueued_at` spelling split. Undefined for a
 * stashed task with no scheduled time, which is the common stashed case.
 */
export function enqueuedAt(s: TaskStatus): Date | undefined {
  const u = unwrapLocked(s);
  if ("Stashed" in u) return parseTs(u.Stashed.enqueue_at);
  if ("Queued" in u) return parseTs(u.Queued.enqueued_at);
  if ("Running" in u) return parseTs(u.Running.enqueued_at);
  if ("Paused" in u) return parseTs(u.Paused.enqueued_at);
  if ("Done" in u) return parseTs(u.Done.enqueued_at);
  return undefined;
}

export function startedAt(s: TaskStatus): Date | undefined {
  const u = unwrapLocked(s);
  if ("Running" in u) return parseTs(u.Running.start);
  if ("Paused" in u) return parseTs(u.Paused.start);
  if ("Done" in u) return parseTs(u.Done.start);
  return undefined;
}

export function endedAt(s: TaskStatus): Date | undefined {
  const u = unwrapLocked(s);
  return "Done" in u ? parseTs(u.Done.end) : undefined;
}

/** Wall-clock runtime: end−start once finished, now−start while it's live. */
export function durationMs(t: Task, now = Date.now()): number | undefined {
  const start = startedAt(t.status);
  if (!start) return undefined;
  const end = endedAt(t.status);
  return (end ? end.getTime() : now) - start.getTime();
}

/* -- labels -------------------------------------------------------------- */

const RESULT_LABELS: Record<ResultKind, string> = {
  success: "success",
  failed: "failed",
  "failed-to-spawn": "failed to spawn",
  killed: "killed",
  errored: "errored",
  "dependency-failed": "dependency failed",
  unknown: "finished",
};

/** A short human label: "running", "failed (127)", "killed", "stashed until …". */
export function statusLabel(t: Task): string {
  const kind = underlyingKind(t.status);
  const lock = isLocked(t.status) ? " (editing)" : "";

  if (kind === "done") {
    const r = taskResult(t.status);
    const rk = resultKind(r) ?? "unknown";
    const code = exitCode(r);
    return `${RESULT_LABELS[rk]}${code !== undefined ? ` (${code})` : ""}${lock}`;
  }
  if (kind === "stashed") {
    const at = enqueuedAt(t.status);
    return `${at ? "scheduled" : "stashed"}${lock}`;
  }
  return `${kind}${lock}`;
}

/**
 * Everything a user might type to find this task. Feeds `List.Item.keywords`,
 * which is how search works — the whole state is already in memory, and the
 * server-side query DSL only matches `command` and `label`.
 */
export function statusKeywords(t: Task): string[] {
  const r = taskResult(t.status);
  const code = exitCode(r);
  return [
    String(t.id),
    t.command,
    t.original_command,
    t.label ?? "",
    t.group,
    t.path,
    underlyingKind(t.status),
    resultKind(r) ?? "",
    code !== undefined ? String(code) : "",
    isLocked(t.status) ? "locked editing" : "",
  ].filter((k) => k.length > 0);
}

/** Collapse whitespace and ellipsize — commands are frequently multi-line pipelines. */
export function oneline(s: string, max = 80): string {
  const flat = s.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/** The tasks map as a list, ordered by id ascending. */
export function taskList(tasks: Record<string, Task>): Task[] {
  return Object.values(tasks).sort((a, b) => a.id - b.id);
}
