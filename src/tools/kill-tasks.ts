/**
 * Stop running tasks.
 *
 * The confirmation reads the queue first so it can name the tasks it is about to
 * kill rather than repeating the request back. "Kill 3 running tasks in wf"
 * with `#7 #8 #9` beside it is something you can check; "kill the wf group" is
 * something you can only trust.
 *
 * It also states the thing pueue's own name doesn't: killing a group pauses it.
 */

import { Action, Tool } from "@raycast/api";

import {
  isPaused,
  isRunning,
  mutate,
  oneline,
  snapshot,
  taskList,
} from "../lib/pueue";
import { resolveConnectionStrict } from "../lib/ai-connection";

type Input = {
  /** Specific task ids. Mutually exclusive with `group` and `all`. */
  taskIds?: number[];
  /**
   * Kill every running task in this group. Note that pueue *also pauses* the
   * group, so nothing further starts until it is resumed.
   */
  group?: string;
  /** Kill everything running, in every group, and pause them all. */
  all?: boolean;
  /** Which daemon to act on. Omit for this machine's own. */
  connection?: string;
};

/** The tasks a request would actually stop — killing only affects live ones. */
async function affected(input: Input) {
  const connection = await resolveConnectionStrict(input.connection);
  const snap = await snapshot({ connection });
  const live = taskList(snap.state.tasks).filter(
    (t) => isRunning(t) || isPaused(t),
  );
  if (input.taskIds && input.taskIds.length > 0) {
    return {
      connection,
      tasks: live.filter((t) => input.taskIds?.includes(t.id)),
    };
  }
  if (input.all) return { connection, tasks: live };
  const group = input.group ?? "default";
  return { connection, tasks: live.filter((t) => t.group === group) };
}

export const confirmation: Tool.Confirmation<Input> = async (input) => {
  const { connection, tasks } = await affected(input);
  const scope = input.all
    ? "every group"
    : input.taskIds && input.taskIds.length > 0
      ? `task${input.taskIds.length === 1 ? "" : "s"} ${input.taskIds.map((id) => `#${id}`).join(", ")}`
      : `group "${input.group ?? "default"}"`;

  return {
    style: Action.Style.Destructive,
    message:
      tasks.length === 0
        ? `Nothing is running in ${scope} on ${connection.name}. Kill anyway?`
        : `Kill ${tasks.length} running task${tasks.length === 1 ? "" : "s"} in ${scope} on ${connection.name}?`,
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
        // Verified in `pueue kill --help`. A confirmation that omits this is
        // agreeing to something other than what happens.
        name: "Also",
        value:
          input.taskIds && input.taskIds.length > 0
            ? undefined
            : "pauses the group, so nothing further starts",
      },
    ],
  };
};

export default async function tool(input: Input) {
  const { connection, tasks } = await affected(input);
  await mutate(
    {
      op: "kill",
      ids: input.taskIds,
      group: input.taskIds?.length ? undefined : input.group,
      all: input.all,
    },
    { connection },
  );

  return {
    killed: tasks.map((t) => t.id),
    connection: connection.name,
    groupPaused: !(input.taskIds && input.taskIds.length > 0),
    note:
      tasks.length === 0
        ? "Nothing was running, so nothing was killed."
        : "Killing by group or with `all` also pauses the group(s); resume with the queue's Start action.",
  };
}
