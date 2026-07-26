/**
 * Remove finished tasks from the queue.
 *
 * "Clean up" is a phrase people use loosely, and here it deletes logs. So the
 * confirmation counts what will go and separates the successes from the
 * failures — agreeing to discard eleven successful builds is a different
 * decision from agreeing to discard the three failures you have not read yet.
 *
 * Deliberately not offered: `reset`. It kills and deletes *everything* in a
 * group, running tasks included, and there is no sentence ambiguous enough to be
 * worth that risk. It stays a deliberate action in the Groups view.
 */

import { Action, Tool } from "@raycast/api";

import {
  isDone,
  isFailed,
  isSuccess,
  mutate,
  snapshot,
  taskList,
} from "../lib/pueue";
import { resolveConnectionStrict } from "../lib/ai-connection";

type Input = {
  /** Only this group. Omit to clean every group. */
  group?: string;
  /**
   * Keep the failures and remove only the successful tasks. Default false.
   * Prefer true when the user has not said they are finished investigating.
   */
  successfulOnly?: boolean;
  /** Which daemon to act on. Omit for this machine's own. */
  connection?: string;
};

async function affected(input: Input) {
  const connection = resolveConnectionStrict(input.connection);
  const snap = await snapshot({ connection });
  let finished = taskList(snap.state.tasks).filter(isDone);
  if (input.group) finished = finished.filter((t) => t.group === input.group);
  const succeeded = finished.filter(isSuccess);
  const failed = finished.filter(isFailed);
  return {
    connection,
    going: input.successfulOnly ? succeeded : finished,
    succeeded,
    failed,
  };
}

export const confirmation: Tool.Confirmation<Input> = async (input) => {
  const { connection, going, failed } = await affected(input);
  const where = input.group ? `group "${input.group}"` : "every group";

  return {
    style: Action.Style.Destructive,
    message:
      going.length === 0
        ? `Nothing finished to clean in ${where} on ${connection.name}.`
        : `Remove ${going.length} finished task${going.length === 1 ? "" : "s"} from ${where} on ${connection.name}? Their logs go too.`,
    info: [
      { name: "Daemon", value: connection.name },
      { name: "Scope", value: where },
      {
        name: "Failures removed",
        value: input.successfulOnly
          ? `none — ${failed.length} kept so their logs stay readable`
          : failed.length > 0
            ? `${failed.length}: ${failed.map((t) => `#${t.id}`).join(" ")}`
            : "none",
      },
      { name: "Running or queued tasks", value: "untouched" },
    ],
  };
};

export default async function tool(input: Input) {
  const { connection, going, failed } = await affected(input);

  await mutate(
    {
      op: "clean",
      group: input.group,
      successfulOnly: input.successfulOnly,
    },
    { connection },
  );

  return {
    removed: going.map((t) => t.id),
    keptFailures: input.successfulOnly ? failed.map((t) => t.id) : [],
    connection: connection.name,
    note: "Logs were deleted along with the tasks. Nothing running or queued was affected.",
  };
}
