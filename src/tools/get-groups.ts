/**
 * Group progress.
 *
 * The tool behind "how far through is the wf group". Returns the same summary
 * the Groups view renders — done/total, the breakdown, the average, the ETA —
 * so the model and the UI can never disagree about a number.
 */

import { snapshot, taskList } from "../lib/pueue";
import { summarizeGroups } from "../lib/group-summary";
import { toAiGroup } from "../lib/ai-shape";
import { connectionNames, resolveConnectionStrict } from "../lib/ai-connection";

type Input = {
  /** Which daemon to ask. Omit for this machine's own. */
  connection?: string;
  /** A single group. Omit for all of them. */
  group?: string;
};

export default async function tool(input: Input) {
  const connection = resolveConnectionStrict(input.connection);
  const snap = await snapshot({ connection });
  const tasks = taskList(snap.state.tasks);

  let groups = summarizeGroups(snap.state.groups, tasks).map(toAiGroup);

  if (input.group) {
    const wanted = input.group.toLowerCase();
    const match = groups.filter((g) => g.name.toLowerCase() === wanted);
    if (match.length === 0) {
      throw new Error(
        `No group named "${input.group}" on ${connection.name}. Groups there: ${groups
          .map((g) => g.name)
          .join(", ")}.`,
      );
    }
    groups = match;
  }

  return {
    connection: connection.name,
    isRemote: connection.remote,
    availableConnections: connectionNames(),
    groups,
    notes: [
      "percentComplete counts every finished task, successful or not — a failed task is finished.",
      "estimatedSecondsRemaining is pending work times the average, and is absent when fewer than two tasks have finished or nothing is pending. It ignores stashed tasks, which wait on a person rather than on the queue.",
      "A group's own status (Running/Paused) is separate from its tasks': a Running group with nothing queued is simply idle.",
    ],
  };
}
