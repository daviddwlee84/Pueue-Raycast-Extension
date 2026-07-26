/**
 * What is in the queue.
 *
 * The general-purpose read. Everything the model might want to filter on is a
 * parameter, because the alternative — returning the whole queue and letting it
 * filter — spends context on tasks nobody asked about.
 */

import { snapshot, taskList } from "../lib/pueue";
import { capped, toAiTask, type AiTask } from "../lib/ai-shape";
import {
  connectionNames,
  noRemotesNote,
  resolveConnectionStrict,
} from "../lib/ai-connection";

type Input = {
  /**
   * Which daemon to ask. Omit for this machine's own. Must be one of the
   * configured connection names.
   */
  connection?: string;
  /** Only tasks in this group. Omit for every group. */
  group?: string;
  /**
   * Only tasks in this state. `failed` covers every unsuccessful outcome —
   * a non-zero exit, killed, errored, failed-to-spawn, or a failed dependency.
   */
  status?:
    | "running"
    | "queued"
    | "paused"
    | "stashed"
    | "failed"
    | "succeeded"
    | "finished";
  /**
   * Case-insensitive substring of the command or label. Use this rather than
   * fetching everything and filtering afterwards.
   */
  search?: string;
  /** Most tasks to return. Defaults to 100, which is also the maximum. */
  limit?: number;
};

export default async function tool(input: Input) {
  const connection = await resolveConnectionStrict(input.connection);
  const snap = await snapshot({ connection, group: input.group });
  const now = Date.now();

  let tasks: AiTask[] = taskList(snap.state.tasks).map((t) => toAiTask(t, now));

  if (input.status) {
    const want = input.status;
    tasks = tasks.filter((t) => {
      if (want === "failed")
        return t.result !== undefined && t.result !== "success";
      if (want === "succeeded") return t.result === "success";
      if (want === "finished") return t.status === "done";
      return t.status === want;
    });
  }

  if (input.search) {
    const needle = input.search.toLowerCase();
    tasks = tasks.filter(
      (t) =>
        t.command.toLowerCase().includes(needle) ||
        (t.label ?? "").toLowerCase().includes(needle),
    );
  }

  // Newest first when truncating: a long queue's recent end is the interesting
  // end, and the id is monotonic per daemon.
  const { items, total, truncated } = capped(
    [...tasks].sort((a, b) => b.id - a.id),
    Math.min(input.limit ?? 100, 100),
  );

  const names = await connectionNames();

  return {
    connection: connection.name,
    isRemote: connection.remote,
    availableConnections: names,
    connectionsNote: noRemotesNote(names),
    matched: total,
    returned: items.length,
    truncated,
    tasks: items,
  };
}
