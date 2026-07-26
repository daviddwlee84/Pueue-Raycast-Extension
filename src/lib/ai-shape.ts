/**
 * The queue, shaped for a language model instead of for a renderer.
 *
 * `Task` off the wire is a two-level externally-tagged enum with a recursive
 * `Locked` variant and a `result` that is sometimes a string and sometimes an
 * object. That shape is a liability in a tool result: a model asked "why did
 * task 6 fail" should not first have to work out that `{"Done":{"result":
 * {"Failed":127}}}` means exit code 127, and every token spent on the wrapper is
 * a token not spent on the answer.
 *
 * So tools return the flattened view — the same one `normalize.ts` already
 * computes for the UI. Nothing here is new logic; it is a projection.
 *
 * One rule this module exists to enforce: **`envs` never appears.** The
 * transport strips it at the parse boundary and nothing here puts it back. It is
 * a snapshot of the submitting shell's environment and may hold secrets; a tool
 * result is sent to a model. `dev-check.ts` asserts the absence rather than
 * trusting it, which is only possible because this module — like `normalize.ts`
 * and `optimistic.ts` — imports from the leaf modules and never from the barrel,
 * so it stays runnable outside Raycast. Connection resolution therefore lives
 * next door in `ai-connection.ts`, which cannot.
 */

import {
  durationMs,
  endedAt,
  enqueuedAt,
  exitCode,
  isLocked,
  resultKind,
  spawnError,
  startedAt,
  taskResult,
  underlyingKind,
  type ResultKind,
  type StatusKind,
} from "./pueue/normalize";
import type { Task } from "./pueue/types";
import type { GroupSummary } from "./group-summary";

/**
 * How many tasks a tool will return at most.
 *
 * A 400-task queue is a real thing — upstream cites it — and handing all of it
 * to a model wastes the context it needs for the actual question. Every tool
 * that truncates says so in its result, because a silently short list is
 * indistinguishable from a short queue.
 */
export const MAX_TASKS = 100;

export interface AiTask {
  id: number;
  command: string;
  /** running | queued | paused | stashed | done — the *underlying* state. */
  status: StatusKind;
  /** Only for finished tasks. `success`, `failed`, `killed`, … */
  result?: ResultKind;
  /** Only for `{ Failed: n }`. A successful task has no exit code on the wire. */
  exitCode?: number;
  /** The OS error from a task that could not be spawned at all. */
  spawnError?: string;
  group: string;
  label?: string;
  workingDirectory: string;
  dependsOn?: number[];
  enqueuedAt?: string;
  startedAt?: string;
  endedAt?: string;
  durationSeconds?: number;
  /** True while `pueue edit` holds the task. It cannot be acted on until done. */
  beingEdited?: boolean;
}

const iso = (d: Date | undefined): string | undefined => d?.toISOString();

export function toAiTask(task: Task, now = Date.now()): AiTask {
  const result = taskResult(task.status);
  const ms = durationMs(task, now);
  // Every field is named explicitly. A spread of `task` would carry whatever
  // pueue adds next straight into a model's context, `envs` included the day
  // someone forgets to strip it.
  return {
    id: task.id,
    command: task.command,
    status: underlyingKind(task.status),
    result: resultKind(result),
    exitCode: exitCode(result),
    spawnError: spawnError(result),
    group: task.group,
    label: task.label ?? undefined,
    workingDirectory: task.path,
    dependsOn: task.dependencies.length > 0 ? task.dependencies : undefined,
    enqueuedAt: iso(enqueuedAt(task.status)),
    startedAt: iso(startedAt(task.status)),
    endedAt: iso(endedAt(task.status)),
    durationSeconds:
      ms === undefined ? undefined : Math.round((ms / 1000) * 10) / 10,
    beingEdited: isLocked(task.status) ? true : undefined,
  };
}

export interface AiGroup {
  name: string;
  /** The group's own state, which is not the same as its tasks'. */
  status: GroupSummary["status"];
  /** How many tasks run at once. `null` is pueue's unlimited. */
  parallelTasks: number | null;
  total: number;
  finished: number;
  running: number;
  queued: number;
  paused: number;
  stashed: number;
  succeeded: number;
  failed: number;
  percentComplete: number;
  failedTaskIds?: number[];
  averageSecondsPerTask?: number;
  /** Undefined when nothing is pending, or when fewer than two tasks have finished. */
  estimatedSecondsRemaining?: number;
}

const seconds = (ms: number | undefined): number | undefined =>
  ms === undefined ? undefined : Math.round(ms / 1000);

export function toAiGroup(s: GroupSummary): AiGroup {
  return {
    name: s.name,
    status: s.status,
    parallelTasks: s.parallel === 0 ? null : s.parallel,
    total: s.total,
    finished: s.finished,
    running: s.running,
    queued: s.queued,
    paused: s.paused,
    stashed: s.stashed,
    succeeded: s.succeeded,
    failed: s.failed,
    percentComplete: Math.round(s.progress * 100),
    failedTaskIds: s.failedIds.length > 0 ? s.failedIds : undefined,
    averageSecondsPerTask: seconds(s.avgMs),
    estimatedSecondsRemaining: seconds(s.etaMs),
  };
}

/**
 * Truncate and say so.
 *
 * The count is always reported, so "3 failed" is never ambiguous between "three
 * failures" and "three failures shown out of many".
 */
export function capped<T>(
  items: T[],
  limit = MAX_TASKS,
): { items: T[]; total: number; truncated: boolean } {
  return {
    items: items.slice(0, limit),
    total: items.length,
    truncated: items.length > limit,
  };
}
