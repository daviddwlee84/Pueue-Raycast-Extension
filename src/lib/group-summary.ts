/**
 * What a group is actually doing, as numbers.
 *
 * A group row used to read `default — running (0/1)`, which is
 * running-tasks-over-parallelism: two numbers that barely move, sitting above a
 * list of six failed tasks. The thing people want from a group is batch
 * progress — submit twenty jobs, then watch them land.
 *
 * Modelled on `pqsum`'s `GroupRec`, which already solved this for the terminal:
 * done/total, a bar, average duration, an ETA, elapsed wall clock, and the ids
 * that failed.
 *
 * Pure and React-free, like `normalize.ts` and `optimistic.ts`, so `just verify`
 * can assert it against the fixture. Every derived value comes from the
 * normalizers rather than from the wire types directly — a `Locked` task must
 * count as whatever it is underneath, not as "locked".
 *
 * Three decisions worth stating, because each one could reasonably have gone
 * the other way:
 *
 *   1. **`total` is every task in the group.** pueue has no concept of a batch;
 *      `pueue clean` between runs is the only boundary that exists. Inventing
 *      one — "since the group last went idle" — would be a guess presented as a
 *      fact, and it would be wrong for exactly the long-running group where you
 *      most need the number.
 *   2. **The ETA ignores stashed tasks.** A stashed task is waiting on a person,
 *      not on the queue, so counting it would inflate the estimate without
 *      bound. It is still in `total`, because it is still work you asked for.
 *   3. **The ETA needs at least two finished tasks.** One sample is a guess
 *      wearing a number's clothes. `undefined` renders as an em dash, which is
 *      honest, and honest beats precise-looking here.
 */

import {
  durationMs,
  endedAt,
  isFailed,
  isPaused,
  isRunning,
  isSuccess,
  startedAt,
  underlyingKind,
} from "./pueue/normalize";
import type { Group, GroupMap, Task } from "./pueue/types";

/** How many finished tasks it takes before an ETA is worth showing. */
const MIN_ETA_SAMPLES = 2;

export interface GroupSummary {
  name: string;
  status: Group["status"];
  /** pueue's `parallel_tasks`. 0 means unlimited. */
  parallel: number;
  total: number;
  running: number;
  queued: number;
  paused: number;
  stashed: number;
  succeeded: number;
  /** Anything that finished as something other than Success. */
  failed: number;
  /** succeeded + failed. A failed task is finished. */
  finished: number;
  /** 0–1, and 0 rather than NaN for an empty group. */
  progress: number;
  /** Mean wall clock of the finished tasks. Undefined with no samples. */
  avgMs?: number;
  /** Estimated time to drain. See the header for what it deliberately excludes. */
  etaMs?: number;
  /** First start to last end, or to `now` while something is still running. */
  elapsedMs?: number;
  /** Ascending, so a "restart these" confirmation can name them in order. */
  failedIds: number[];
}

/**
 * Summarise one group.
 *
 * `tasks` may be the whole queue; it is filtered here. Passing a group with no
 * tasks is normal — a freshly created group is empty, and it must not divide by
 * zero on the way to saying so.
 */
export function summarizeGroup(
  name: string,
  group: Group,
  tasks: readonly Task[],
  now = Date.now(),
): GroupSummary {
  const mine = tasks.filter((t) => t.group === name);

  let running = 0;
  let queued = 0;
  let paused = 0;
  let stashed = 0;
  let succeeded = 0;
  let failed = 0;
  const failedIds: number[] = [];
  const finishedDurations: number[] = [];
  let firstStart: number | undefined;
  let lastEnd: number | undefined;
  let anyUnfinished = false;

  for (const task of mine) {
    // Through underlyingKind, so a task held by `pueue edit` still counts as
    // whatever it will go back to being.
    switch (underlyingKind(task.status)) {
      case "running":
        running += 1;
        break;
      case "queued":
        queued += 1;
        break;
      case "paused":
        paused += 1;
        break;
      case "stashed":
        stashed += 1;
        break;
      default:
        break;
    }

    if (isRunning(task) || isPaused(task)) anyUnfinished = true;

    const start = startedAt(task.status);
    if (start && (firstStart === undefined || start.getTime() < firstStart)) {
      firstStart = start.getTime();
    }
    const end = endedAt(task.status);
    if (end && (lastEnd === undefined || end.getTime() > lastEnd)) {
      lastEnd = end.getTime();
    }

    if (isSuccess(task) || isFailed(task)) {
      if (isSuccess(task)) {
        succeeded += 1;
      } else {
        failed += 1;
        failedIds.push(task.id);
      }
      // Only finished tasks contribute a duration sample. A running one would
      // contribute its elapsed-so-far, which drags the average — and therefore
      // the ETA — down for exactly as long as it keeps running.
      const ms = durationMs(task, now);
      if (ms !== undefined && ms >= 0) finishedDurations.push(ms);
    }
  }

  failedIds.sort((a, b) => a - b);

  const total = mine.length;
  const finished = succeeded + failed;
  const avgMs =
    finishedDurations.length > 0
      ? finishedDurations.reduce((a, b) => a + b, 0) / finishedDurations.length
      : undefined;

  // Everything the queue still owes you on its own. Stashed is excluded — see
  // the header — and so is anything already finished.
  const pending = running + queued + paused;
  const etaMs =
    avgMs !== undefined &&
    finishedDurations.length >= MIN_ETA_SAMPLES &&
    pending > 0
      ? // parallel === 0 is pueue's "unlimited"; a slot count of zero would
        // divide by zero, and treating it as one is the conservative estimate.
        (pending * avgMs) / Math.max(1, group.parallel_tasks)
      : undefined;

  // Elapsed runs to `now` only while something is actually accruing time. With
  // nothing running, the span freezes at the last end — a group with tasks
  // still queued behind a paused slot has not been running for three hours.
  const elapsedEnd = anyUnfinished ? now : lastEnd;
  const elapsedMs =
    firstStart !== undefined &&
    elapsedEnd !== undefined &&
    elapsedEnd >= firstStart
      ? elapsedEnd - firstStart
      : undefined;

  return {
    name,
    status: group.status,
    parallel: group.parallel_tasks,
    total,
    running,
    queued,
    paused,
    stashed,
    succeeded,
    failed,
    finished,
    progress: total > 0 ? finished / total : 0,
    avgMs,
    etaMs,
    elapsedMs,
    failedIds,
  };
}

/** Every group in the map, sorted by name. */
export function summarizeGroups(
  groups: GroupMap,
  tasks: readonly Task[],
  now = Date.now(),
): GroupSummary[] {
  return Object.entries(groups)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, group]) => summarizeGroup(name, group, tasks, now));
}

export interface OverallSummary {
  total: number;
  finished: number;
  failed: number;
  running: number;
  progress: number;
}

/** The whole queue at once, for a section heading. */
export function summarizeAll(
  summaries: readonly GroupSummary[],
): OverallSummary {
  const add = (pick: (s: GroupSummary) => number) =>
    summaries.reduce((sum, s) => sum + pick(s), 0);
  const total = add((s) => s.total);
  const finished = add((s) => s.finished);
  return {
    total,
    finished,
    failed: add((s) => s.failed),
    running: add((s) => s.running),
    progress: total > 0 ? finished / total : 0,
  };
}

/**
 * `████░░░░░░` — for the menu bar, which has no room for a real progress view.
 *
 * Block Elements rather than pqsum's `[====----]`: the menu renders in the
 * system font, where `=` and `-` are proportional and the bar visibly changes
 * width as it fills. U+2588 and U+2591 are the same advance width in every font
 * that has them.
 */
export function progressBar(progress: number, width = 10): string {
  const clamped = Math.max(0, Math.min(1, progress));
  const filled = Math.round(clamped * width);
  return "█".repeat(filled) + "░".repeat(width - filled);
}

/** `40%`, rounded — never `39.99999999%`. */
export function progressPercent(progress: number): string {
  return `${Math.round(Math.max(0, Math.min(1, progress)) * 100)}%`;
}

/** `0` is pueue's unlimited, and reads far better as a symbol than as a zero. */
export function parallelLabel(parallel: number): string {
  return parallel === 0 ? "∞" : String(parallel);
}

/**
 * The one-line summary: `5/12 done · 1 running · 2 failed`.
 *
 * Zero terms are dropped rather than shown as zeroes, so the line says only
 * what is true. An empty group has nothing to report and says so.
 */
export function summaryLine(s: GroupSummary): string {
  if (s.total === 0) return "no tasks";
  const parts = [`${s.finished}/${s.total} done`];
  if (s.running > 0) parts.push(`${s.running} running`);
  if (s.queued > 0) parts.push(`${s.queued} queued`);
  if (s.paused > 0) parts.push(`${s.paused} paused`);
  if (s.stashed > 0) parts.push(`${s.stashed} stashed`);
  if (s.failed > 0) parts.push(`${s.failed} failed`);
  return parts.join(" · ");
}
