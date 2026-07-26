/**
 * Queue a command.
 *
 * The only tool that creates work rather than reporting it, and the one worth
 * having: "queue a release build in the wf group on lab" is a sentence, and
 * everything it needs — the command, the group, the daemon — is in it.
 *
 * The confirmation is not optional. A shell command assembled from a sentence is
 * exactly the thing a person should read before it runs, and the confirmation
 * shows it verbatim alongside the machine it will run on.
 */

import { Action, Tool } from "@raycast/api";

import { mutate, oneline } from "../lib/pueue";
import { resolveConnectionStrict } from "../lib/ai-connection";

type Input = {
  /**
   * The shell command line, exactly as it would be typed. pueue hands it to
   * `sh -c` itself, so pipes, redirections, `&&` and quoting all work — do not
   * escape or wrap it.
   */
  command: string;
  /** Which daemon to queue it on. Omit for this machine's own. */
  connection?: string;
  /** The group to queue into. Omit for `default`. The group must already exist. */
  group?: string;
  /** A short label, shown beside the task in the queue. */
  label?: string;
  /**
   * Where to run it. Must exist on the machine that will run the task — for a
   * remote connection that is the remote filesystem, not this one.
   */
  workingDirectory?: string;
  /** Task ids this one waits for. A failed dependency yields DependencyFailed. */
  after?: number[];
  /** Queue it without running: it sits in the stash until enqueued by hand. */
  stashed?: boolean;
  /** Start immediately, ignoring the group's parallelism limit. */
  immediate?: boolean;
};

export const confirmation: Tool.Confirmation<Input> = async (input) => {
  const connection = resolveConnectionStrict(input.connection);
  return {
    // Not Destructive: this adds work rather than destroying any. It still
    // confirms, because the command was assembled from prose.
    style: Action.Style.Regular,
    message: `Queue this command on ${connection.name}?`,
    info: [
      { name: "Command", value: input.command },
      { name: "Daemon", value: connection.name },
      { name: "Group", value: input.group ?? "default" },
      { name: "Directory", value: input.workingDirectory },
      { name: "Label", value: input.label },
      {
        name: "Waits for",
        value: input.after?.map((id) => `#${id}`).join(", "),
      },
      {
        name: "Start",
        value: input.stashed
          ? "stashed — will not run until enqueued"
          : input.immediate
            ? "immediately, ignoring the parallelism limit"
            : undefined,
      },
    ],
  };
};

export default async function tool(input: Input) {
  const connection = resolveConnectionStrict(input.connection);
  const id = await mutate(
    {
      op: "add",
      command: input.command,
      group: input.group,
      label: input.label,
      workingDirectory: input.workingDirectory,
      after: input.after,
      stashed: input.stashed,
      immediate: input.immediate,
    },
    { connection },
  );

  return {
    queued: true,
    taskId: id ?? null,
    connection: connection.name,
    group: input.group ?? "default",
    command: oneline(input.command, 200),
  };
}
