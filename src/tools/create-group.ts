/**
 * Make a group.
 *
 * The missing piece in a batch: pueue's idiom is one group per batch — it is the
 * only unit that carries its own parallelism limit and its own progress — and
 * `add --group nightly` fails outright if `nightly` does not exist
 * ("Group nightly doesn't exists. Use one of these: [...]"). Without this, every
 * "queue these five jobs into a new group" request dead-ends on the first call.
 *
 * Confirmed, but not destructive: creating a group takes nothing away. The
 * confirmation exists because parallelism is the number people get wrong, and
 * seeing it before twenty jobs start is worth one keystroke.
 */

import { Action, Tool } from "@raycast/api";

import { mutate, snapshot } from "../lib/pueue";
import { resolveConnectionStrict } from "../lib/ai-connection";

type Input = {
  /** The group name. Must not already exist. */
  name: string;
  /**
   * How many tasks the group runs at once. Defaults to 1, which is what you
   * want for jobs that compete for the same resource — a GPU, a database, a
   * lock. Use 0 for unlimited only when the jobs genuinely do not interfere.
   */
  parallelTasks?: number;
  /** Which daemon to create it on. Omit for this machine's own. */
  connection?: string;
};

export const confirmation: Tool.Confirmation<Input> = async (input) => {
  const connection = await resolveConnectionStrict(input.connection);
  const parallel = input.parallelTasks ?? 1;
  return {
    style: Action.Style.Regular,
    message: `Create the group "${input.name}" on ${connection.name}?`,
    info: [
      { name: "Daemon", value: connection.name },
      {
        name: "Runs at once",
        value: parallel === 0 ? "unlimited" : String(parallel),
      },
    ],
  };
};

export default async function tool(input: Input) {
  const connection = await resolveConnectionStrict(input.connection);

  // pueue's own error for a duplicate is fine, but catching it here lets the
  // model carry on and queue into the existing group rather than stopping.
  const snap = await snapshot({ connection });
  if (input.name in snap.state.groups) {
    return {
      created: false,
      name: input.name,
      connection: connection.name,
      note: `The group "${input.name}" already exists on ${connection.name}. Queue into it as it is, or pick another name.`,
    };
  }

  await mutate(
    { op: "group-add", name: input.name, parallel: input.parallelTasks },
    { connection },
  );

  return {
    created: true,
    name: input.name,
    parallelTasks: input.parallelTasks ?? 1,
    connection: connection.name,
  };
}
