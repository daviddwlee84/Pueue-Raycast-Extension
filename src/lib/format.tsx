/**
 * Presentation. Kept apart from `normalize.ts` so that module stays React-free
 * and directly testable; everything here is about how a task *looks*.
 */

import { Color, Icon, type Image } from "@raycast/api";
import { getProgressIcon } from "@raycast/utils";

import type { GroupSummary } from "./group-summary";
import {
  endedAt,
  exitCode,
  isLocked,
  resultKind,
  startedAt,
  statusKind,
  taskResult,
  underlyingKind,
  type ResultKind,
  type StatusKind,
} from "./pueue";
import type { Group, Task } from "./pueue";

/**
 * One glyph per state, each tinted.
 *
 * Colour carries the meaning at a glance and the glyph disambiguates the
 * failures, which all read red: a killed task and a task that exited 127 are
 * both bad, but only one of them was your doing.
 */
export function statusIcon(task: Task): Image.ImageLike {
  const kind = underlyingKind(task.status);

  if (kind === "done") {
    switch (resultKind(taskResult(task.status))) {
      case "success":
        return { source: Icon.CheckCircle, tintColor: Color.Green };
      case "killed":
        return { source: Icon.Stop, tintColor: Color.Red };
      case "dependency-failed":
        return { source: Icon.MinusCircle, tintColor: Color.Red };
      case "failed-to-spawn":
        return { source: Icon.QuestionMark, tintColor: Color.Red };
      case "errored":
        return { source: Icon.Warning, tintColor: Color.Red };
      default:
        return { source: Icon.XMarkCircle, tintColor: Color.Red };
    }
  }

  switch (kind) {
    case "running":
      return { source: Icon.CircleProgress, tintColor: Color.Blue };
    case "paused":
      return { source: Icon.Pause, tintColor: Color.Orange };
    case "queued":
      return { source: Icon.Clock, tintColor: Color.SecondaryText };
    case "stashed":
      return { source: Icon.Tray, tintColor: Color.SecondaryText };
    case "locked":
      return { source: Icon.Lock, tintColor: Color.Purple };
    default:
      return { source: Icon.Circle, tintColor: Color.SecondaryText };
  }
}

/** The tag colour for a task's status accessory. */
export function statusColor(task: Task): Color {
  const kind = underlyingKind(task.status);
  if (kind === "done") {
    return resultKind(taskResult(task.status)) === "success"
      ? Color.Green
      : Color.Red;
  }
  switch (kind) {
    case "running":
      return Color.Blue;
    case "paused":
      return Color.Orange;
    case "locked":
      return Color.Purple;
    default:
      return Color.SecondaryText;
  }
}

export function groupIcon(group: Group): Image.ImageLike {
  switch (group.status) {
    case "Running":
      return { source: Icon.Play, tintColor: Color.Green };
    case "Paused":
      return { source: Icon.Pause, tintColor: Color.Orange };
    default:
      return { source: Icon.Circle, tintColor: Color.SecondaryText };
  }
}

/**
 * The one colour that describes a whole group.
 *
 * Ordered by what you would want to be told first. A failure outranks a pause,
 * because a paused group is a decision and a failed task is a surprise; a pause
 * outranks activity, because a paused group is not going to finish on its own.
 */
export function groupColor(s: GroupSummary): Color {
  if (s.failed > 0) return Color.Red;
  if (s.status === "Paused") return Color.Orange;
  if (s.running > 0) return Color.Blue;
  if (s.total > 0 && s.finished === s.total) return Color.Green;
  return Color.SecondaryText;
}

/**
 * A filled ring rather than a play/pause glyph.
 *
 * The ring is the only thing on the row that shows *progress*, which is what a
 * group is for. The tint carries the pause state the glyph used to, so nothing
 * is lost — and an empty group keeps the old glyph, because a 0 % ring on a
 * group with no tasks reads as "stuck" rather than "nothing here".
 */
export function groupProgressIcon(s: GroupSummary): Image.ImageLike {
  if (s.total === 0) {
    return groupIcon({ status: s.status, parallel_tasks: s.parallel });
  }
  return getProgressIcon(s.progress, groupColor(s));
}

/**
 * A duration a person can read at a glance.
 *
 * Sub-minute gets decimals because most queued shell commands finish there and
 * "0s" would be useless; past a minute the seconds stop mattering.
 */
export function formatDuration(ms: number | undefined): string | undefined {
  if (ms === undefined || ms < 0) return undefined;
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(Math.floor(s % 60)).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${String(m % 60).padStart(2, "0")}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

/** "just now" / "4m ago" / "in 2h" — signed, because stashed tasks point forward. */
export function formatRelative(
  date: Date | undefined,
  now = Date.now(),
): string | undefined {
  if (!date) return undefined;
  const diff = date.getTime() - now;
  const abs = Math.abs(diff);
  if (abs < 45_000) return diff < 0 ? "just now" : "in a moment";

  const units: [number, string][] = [
    [86_400_000, "d"],
    [3_600_000, "h"],
    [60_000, "m"],
  ];
  for (const [ms, suffix] of units) {
    if (abs >= ms) {
      const n = Math.round(abs / ms);
      return diff < 0 ? `${n}${suffix} ago` : `in ${n}${suffix}`;
    }
  }
  return diff < 0 ? "just now" : "in a moment";
}

export function formatWhen(date: Date | undefined): string | undefined {
  return date?.toLocaleString();
}

/**
 * The accessory line: how long it took, or how long it has been going.
 *
 * A running task shows elapsed time, which is the number you actually want when
 * deciding whether to kill it.
 */
export function durationAccessory(
  task: Task,
  now = Date.now(),
): string | undefined {
  const start = startedAt(task.status);
  if (!start) return undefined;
  const end = endedAt(task.status);
  return formatDuration((end ? end.getTime() : now) - start.getTime());
}

const KIND_TITLES: Record<StatusKind, string> = {
  running: "Running",
  paused: "Paused",
  queued: "Queued",
  stashed: "Stashed",
  locked: "Locked",
  done: "Done",
  unknown: "Unknown",
};

const RESULT_TITLES: Record<ResultKind, string> = {
  success: "Success",
  failed: "Failed",
  "failed-to-spawn": "Failed to Spawn",
  killed: "Killed",
  errored: "Errored",
  "dependency-failed": "Dependency Failed",
  unknown: "Finished",
};

/**
 * Section headings, ordered by how much they want your attention.
 *
 * Failures come before successes because a queue you are looking at is usually
 * a queue where something went wrong.
 */
export const SECTION_ORDER = [
  "running",
  "paused",
  "locked",
  "queued",
  "stashed",
  "failed",
  "done",
] as const;

export type SectionKey = (typeof SECTION_ORDER)[number];

export const SECTION_TITLES: Record<SectionKey, string> = {
  running: "Running",
  paused: "Paused",
  locked: "Locked (editing)",
  queued: "Queued",
  stashed: "Stashed",
  failed: "Failed",
  done: "Finished",
};

/** Which section a task belongs to. Failures split out of "done". */
export function sectionOf(task: Task): SectionKey {
  const kind = underlyingKind(task.status);
  if (kind === "done") {
    return resultKind(taskResult(task.status)) === "success"
      ? "done"
      : "failed";
  }
  // A locked task is shown under its own heading — it can't be acted on until
  // the edit finishes, so grouping it with its underlying state would mislead.
  if (isLocked(task.status)) return "locked";
  return kind === "unknown" ? "queued" : (kind as SectionKey);
}

/** The status accessory tag text, e.g. "Failed 127". */
export function statusTag(task: Task): string {
  const kind = underlyingKind(task.status);
  if (kind !== "done") return KIND_TITLES[statusKind(task.status)];
  const r = taskResult(task.status);
  const code = exitCode(r);
  const base = RESULT_TITLES[resultKind(r) ?? "unknown"];
  return code !== undefined ? `${base} ${code}` : base;
}
