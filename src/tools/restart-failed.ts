/**
 * Run failed tasks again.
 *
 * The two forms are genuinely different operations and the confirmation says
 * which one is happening: a plain restart mints new ids and leaves the originals
 * with their logs, an in-place restart reuses the ids and **overwrites** them.
 * Losing the log of a failure you were about to read is the kind of thing that
 * should never happen because a sentence was ambiguous.
 */

import { Action, Tool } from "@raycast/api";

import { isFailed, mutate, oneline, snapshot, taskList } from "../lib/pueue";
import { resolveConnectionStrict } from "../lib/ai-connection";

type Input = {
  /** Specific task ids. Mutually exclusive with `group` and `allFailed`. */
  taskIds?: number[];
  /** Every failed task in this group. */
  group?: string;
  /** Every failed task in every group. */
  allFailed?: boolean;
  /**
   * Reuse the existing task ids instead of creating new ones. This
   * **overwrites the previous output** of every task restarted. Default false,
   * which is the safe choice: the originals keep their logs.
   */
  inPlace?: boolean;
  /** Which daemon to act on. Omit for this machine's own. */
  connection?: string;
};

async function affected(input: Input) {
  const connection = await resolveConnectionStrict(input.connection);
  const snap = await snapshot({ connection });
  const failed = taskList(snap.state.tasks).filter(isFailed);
  if (input.taskIds && input.taskIds.length > 0) {
    return {
      connection,
      tasks: taskList(snap.state.tasks).filter((t) =>
        input.taskIds?.includes(t.id),
      ),
    };
  }
  if (input.allFailed) return { connection, tasks: failed };
  const group = input.group ?? "default";
  return { connection, tasks: failed.filter((t) => t.group === group) };
}

export const confirmation: Tool.Confirmation<Input> = async (input) => {
  const { connection, tasks } = await affected(input);
  const scope = input.allFailed
    ? "every group"
    : input.taskIds && input.taskIds.length > 0
      ? "the given tasks"
      : `group "${input.group ?? "default"}"`;

  return {
    // Destructive only in place — that is the variant that destroys something.
    style: input.inPlace ? Action.Style.Destructive : Action.Style.Regular,
    message: input.inPlace
      ? `Restart ${tasks.length} task${tasks.length === 1 ? "" : "s"} in place on ${connection.name}? Their existing output will be overwritten.`
      : `Restart ${tasks.length} failed task${tasks.length === 1 ? "" : "s"} in ${scope} on ${connection.name}?`,
    info: [
      { name: "Daemon", value: connection.name },
      {
        name: "Tasks",
        value:
          tasks.length > 0
            ? tasks.map((t) => `#${t.id} ${oneline(t.command, 40)}`).join("\n")
            : undefined,
      },
      {
        name: "Mode",
        value: input.inPlace
          ? "in place — same ids, previous logs overwritten"
          : "new tasks — the originals keep their ids and logs",
      },
    ],
  };
};

export default async function tool(input: Input) {
  const { connection, tasks } = await affected(input);

  if (tasks.length === 0) {
    // pueue accepts `--failed-in-group` on a group with nothing to restart and
    // exits 0 in silence. Saying so beats reporting a success that did nothing.
    return {
      restarted: [],
      connection: connection.name,
      note: `Nothing failed in ${input.allFailed ? "any group" : `"${input.group ?? "default"}"`} on ${connection.name}, so nothing was restarted.`,
    };
  }

  await mutate(
    {
      op: "restart",
      ids: input.taskIds,
      failedInGroup:
        input.taskIds?.length || input.allFailed
          ? undefined
          : (input.group ?? "default"),
      allFailed: input.allFailed,
      inPlace: input.inPlace,
    },
    { connection },
  );

  return {
    restarted: tasks.map((t) => t.id),
    connection: connection.name,
    inPlace: input.inPlace === true,
    note: input.inPlace
      ? "The tasks kept their ids and their previous output was overwritten."
      : "New tasks were created with new ids; the originals keep their logs. Read the queue again to get the new ids.",
  };
}
