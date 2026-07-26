/**
 * One task, with its output.
 *
 * The tool behind "why did task 6 fail". The metadata alone rarely answers it —
 * an exit code says *that* something failed — so this returns the log with it,
 * in one call rather than making the model chain two.
 */

import { readLogText, snapshot } from "../lib/pueue";
import { hasEverRun } from "../lib/pueue";
import { toAiTask } from "../lib/ai-shape";
import { resolveConnectionStrict } from "../lib/ai-connection";

/** Trailing lines returned by default. Errors are at the end of a log, not the start. */
const DEFAULT_LINES = 50;
const MAX_LINES = 500;

type Input = {
  /** The task id, as shown in the queue. Ids are per-daemon. */
  taskId: number;
  /** Which daemon the id belongs to. Omit for this machine's own. */
  connection?: string;
  /**
   * How many trailing lines of output to return. Defaults to 50, capped at 500.
   * The end of a log is where a failure explains itself.
   */
  lines?: number;
};

export default async function tool(input: Input) {
  const connection = resolveConnectionStrict(input.connection);
  const snap = await snapshot({ connection });
  const raw = snap.state.tasks[String(input.taskId)];

  if (!raw) {
    // Naming the range is more useful than "not found": ids are per-daemon, and
    // being on the wrong daemon is the likeliest reason for a miss.
    const ids = Object.values(snap.state.tasks).map((t) => t.id);
    throw new Error(
      `No task ${input.taskId} on ${connection.name}. ` +
        (ids.length > 0
          ? `That daemon has ids ${Math.min(...ids)}–${Math.max(...ids)}. Task ids are per-daemon; check the connection.`
          : "That daemon has no tasks at all."),
    );
  }

  const task = toAiTask(raw);

  // A task that never started has no log file, and asking anyway does not fail:
  // pueue exits 0 with its own error text sitting in the output field. Skipping
  // the call is both faster and the only way to avoid returning a Rust I/O error
  // as though the task had printed it.
  if (!hasEverRun(raw)) {
    return {
      connection: connection.name,
      task,
      output: null,
      note: "This task has not started, so it has no output yet.",
    };
  }

  const lines = Math.min(Math.max(1, input.lines ?? DEFAULT_LINES), MAX_LINES);
  const log = await readLogText(input.taskId, { lines, connection });

  return {
    connection: connection.name,
    task,
    output: log?.text ?? null,
    outputTruncatedToLastLines: log ? lines : undefined,
    note:
      log === undefined
        ? "The task ran but produced no output."
        : task.result !== undefined && task.result !== "success"
          ? "This task failed. The cause is usually in the last few lines above."
          : undefined,
  };
}
