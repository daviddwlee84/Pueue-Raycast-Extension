/**
 * What the queue will look like once a mutation lands.
 *
 * This exists because of one pueue behaviour: the daemon acknowledges a request
 * *before* its own update loop has applied it, so the very next
 * `pueue status --json` can still report the old state. `useCachedPromise`'s
 * `mutate` revalidates immediately by default, which would read that stale
 * state and visually undo the optimistic update — the row would flip, flip
 * back, and then flip forward again a minute later.
 *
 * So the UI paints this prediction, suppresses the automatic revalidate, and
 * reconciles on a short delay instead.
 *
 * Pure and total: no I/O, no React, and never throws. A mutation whose outcome
 * cannot be predicted (a new task id, a queue reordering) returns the state
 * unchanged rather than guessing — a wrong prediction is worse than none.
 */

// Imported from the leaf modules rather than the `pueue` barrel on purpose:
// the barrel pulls in the transport and therefore @raycast/api, and this module
// must stay runnable outside Raycast so `just verify` can assert it.
import {
  enqueuedAt,
  isDone,
  isSuccess,
  startedAt,
  underlyingKind,
} from "./pueue/normalize";
import type { Mutation } from "./pueue/transport";
import type { Group, State, Task, TaskResult } from "./pueue/types";

const iso = (d: Date | undefined, fallback: string): string =>
  (d ?? new Date(fallback)).toISOString();

function clone(state: State): State {
  return { tasks: { ...state.tasks }, groups: { ...state.groups } };
}

/**
 * Which tasks a mutation touches.
 *
 * pueue accepts explicit ids, a whole group, or everything; `--group` and
 * `--all` are resolved against the states the command actually acts on, so
 * "pause the group" doesn't optimistically pause tasks that already finished.
 */
function targets(
  state: State,
  m: { ids?: number[]; group?: string; all?: boolean },
): Set<number> {
  if (m.ids && m.ids.length > 0) return new Set(m.ids);
  const all = Object.values(state.tasks);
  const scoped = m.all
    ? all
    : all.filter((t) => t.group === (m.group ?? "default"));
  return new Set(scoped.map((t) => t.id));
}

function mapTasks(
  state: State,
  ids: Set<number>,
  fn: (t: Task) => Task | undefined,
): State {
  const next = clone(state);
  for (const [key, task] of Object.entries(state.tasks)) {
    if (!ids.has(task.id)) continue;
    const updated = fn(task);
    if (updated === undefined) delete next.tasks[key];
    else next.tasks[key] = updated;
  }
  return next;
}

function setGroupStatus(
  state: State,
  name: string | undefined,
  all: boolean | undefined,
  status: Group["status"],
): State {
  const next = clone(state);
  for (const [key, group] of Object.entries(state.groups)) {
    if (all || key === (name ?? "default"))
      next.groups[key] = { ...group, status };
  }
  return next;
}

/** Finish a task now, with the given result. Preserves its existing timestamps. */
function finish(task: Task, result: TaskResult): Task {
  const now = new Date().toISOString();
  return {
    ...task,
    status: {
      Done: {
        enqueued_at: iso(enqueuedAt(task.status), now),
        start: iso(startedAt(task.status), now),
        end: now,
        result,
      },
    },
  };
}

export function applyMutation(state: State | undefined, m: Mutation): State {
  if (!state) return { tasks: {}, groups: {} };
  const now = new Date().toISOString();

  switch (m.op) {
    case "kill": {
      const ids = targets(state, m);
      // Killing by group or with --all also pauses the group(s) — pueue's own
      // documented behaviour, and the reason the confirmation copy says so.
      const withStatuses = mapTasks(state, ids, (t) =>
        ["running", "paused"].includes(underlyingKind(t.status))
          ? finish(t, "Killed")
          : t,
      );
      return m.ids && m.ids.length > 0
        ? withStatuses
        : setGroupStatus(withStatuses, m.group, m.all, "Paused");
    }

    case "start": {
      const resumed = mapTasks(state, targets(state, m), (t) => {
        const kind = underlyingKind(t.status);
        if (kind === "paused") {
          return {
            ...t,
            status: {
              Running: {
                enqueued_at: iso(enqueuedAt(t.status), now),
                start: iso(startedAt(t.status), now),
              },
            },
          };
        }
        // A queued task only starts if a slot is free; predicting that needs the
        // scheduler, so leave it and let the reconcile show the truth.
        return t;
      });
      return m.ids && m.ids.length > 0
        ? resumed
        : setGroupStatus(resumed, m.group, m.all, "Running");
    }

    case "pause": {
      const paused = mapTasks(state, targets(state, m), (t) =>
        underlyingKind(t.status) === "running"
          ? {
              ...t,
              status: {
                Paused: {
                  enqueued_at: iso(enqueuedAt(t.status), now),
                  start: iso(startedAt(t.status), now),
                },
              },
            }
          : t,
      );
      return m.ids && m.ids.length > 0
        ? paused
        : setGroupStatus(paused, m.group, m.all, "Paused");
    }

    case "stash":
      return mapTasks(state, targets(state, m), (t) =>
        underlyingKind(t.status) === "queued"
          ? // `--delay` accepts expressions like "wednesday 10:30pm" that only
            // pueue can resolve, so the scheduled time is left unset and the
            // reconcile fills it in a moment later.
            { ...t, status: { Stashed: { enqueue_at: null } } }
          : t,
      );

    case "enqueue":
      return mapTasks(state, targets(state, m), (t) =>
        underlyingKind(t.status) === "stashed"
          ? { ...t, status: { Queued: { enqueued_at: now } } }
          : t,
      );

    case "remove":
      return mapTasks(state, new Set(m.ids), () => undefined);

    case "clean": {
      const next = clone(state);
      for (const [key, task] of Object.entries(state.tasks)) {
        if (!isDone(task)) continue;
        if (m.group && task.group !== m.group) continue;
        if (m.successfulOnly && !isSuccess(task)) continue;
        delete next.tasks[key];
      }
      return next;
    }

    case "restart":
      // Only an in-place restart keeps the id. A fresh restart mints a new task
      // whose id we cannot know until the daemon answers.
      return m.inPlace
        ? mapTasks(state, new Set(m.ids), (t) => ({
            ...t,
            status: { Queued: { enqueued_at: now } },
          }))
        : state;

    case "parallel": {
      const next = clone(state);
      const name = m.group ?? "default";
      const group = next.groups[name];
      if (group) next.groups[name] = { ...group, parallel_tasks: m.count };
      return next;
    }

    case "group-add": {
      const next = clone(state);
      next.groups[m.name] = {
        status: "Running",
        parallel_tasks: m.parallel ?? 1,
      };
      return next;
    }

    case "group-remove": {
      const next = clone(state);
      delete next.groups[m.name];
      // pueue moves the group's tasks to `default` rather than deleting them.
      for (const [key, task] of Object.entries(state.tasks)) {
        if (task.group === m.name)
          next.tasks[key] = { ...task, group: "default" };
      }
      return next;
    }

    case "reset": {
      const next = clone(state);
      for (const [key, task] of Object.entries(state.tasks)) {
        if (!m.groups || m.groups.includes(task.group)) delete next.tasks[key];
      }
      return next;
    }

    // add mints an id we don't know; switch reorders the queue without changing
    // any field we render; send changes nothing observable.
    case "add":
    case "switch":
    case "send":
      return state;
  }
}
