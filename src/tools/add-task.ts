/**
 * Queue a command.
 *
 * The only tool that creates work rather than reporting it, and the one worth
 * having: "queue a release build in the nightly group" is a sentence, and
 * everything it needs — the command, the group, the daemon — is in it.
 *
 * The confirmation is not optional. A shell command assembled from a sentence is
 * exactly the thing a person should read before it runs, and the confirmation
 * shows it verbatim alongside the machine and the directory it will run in.
 *
 * That directory is the sharp edge here. `pueue add` with no
 * `--working-directory` inherits the *client's* current directory, and the
 * client is a subprocess of Raycast — measured to be `/`. A model that queued
 * `make build` without one would be queueing it at the filesystem root, which
 * then fails in a way that looks like the build is broken. So a directory is
 * always resolved and always shown. See `lib/last-used.ts`.
 */

import { Action, Tool } from "@raycast/api";

import { mutate, oneline } from "../lib/pueue";
import { defaultWorkingDirectory, rememberLastUsed } from "../lib/last-used";
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
   * Where to run it. **Always set this for a command that depends on where it
   * runs** — `make`, `npm`, `cargo`, `git`, anything using a relative path. If
   * the user has not said where, ask rather than guessing: the fallback is the
   * last directory they queued into, which may belong to a different project.
   * The path must exist on the machine that will run the task, which for a
   * remote connection is the remote filesystem, not this one.
   */
  workingDirectory?: string;
  /** Task ids this one waits for. A failed dependency yields DependencyFailed. */
  after?: number[];
  /** Queue it without running: it sits in the stash until enqueued by hand. */
  stashed?: boolean;
  /** Start immediately, ignoring the group's parallelism limit. */
  immediate?: boolean;
};

/** How the directory reads in a confirmation, including whose choice it was. */
function directoryLabel(
  chosen: string | undefined,
  resolved: string | undefined,
  connectionName: string,
): string {
  if (chosen) return chosen;
  if (resolved) return `${resolved} (last used — nobody chose)`;
  return `${connectionName}'s home directory`;
}

export const confirmation: Tool.Confirmation<Input> = async (input) => {
  const connection = await resolveConnectionStrict(input.connection);
  const resolved =
    input.workingDirectory ?? (await defaultWorkingDirectory(connection));
  return {
    // Not Destructive: this adds work rather than destroying any. It still
    // confirms, because the command was assembled from prose.
    style: Action.Style.Regular,
    message: `Queue this command on ${connection.name}?`,
    info: [
      { name: "Command", value: input.command },
      { name: "Daemon", value: connection.name },
      { name: "Group", value: input.group ?? "default" },
      {
        // Always shown, never omitted. Running in the wrong directory is the
        // failure this tool is most likely to cause, and the only place it can
        // be caught is here.
        name: "Directory",
        value: directoryLabel(
          input.workingDirectory,
          resolved,
          connection.name,
        ),
      },
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
  const connection = await resolveConnectionStrict(input.connection);
  const resolved =
    input.workingDirectory ?? (await defaultWorkingDirectory(connection));

  const id = await mutate(
    {
      op: "add",
      command: input.command,
      group: input.group,
      label: input.label,
      workingDirectory: resolved,
      after: input.after,
      stashed: input.stashed,
      immediate: input.immediate,
    },
    { connection },
  );

  // Only remember a directory the caller actually chose. Writing the fallback
  // back would turn one unspecified submission into the default for every later
  // one, which is how a wrong guess becomes permanent.
  if (input.workingDirectory) {
    await rememberLastUsed(connection.name, { cwd: input.workingDirectory });
  }

  return {
    queued: true,
    taskId: id ?? null,
    connection: connection.name,
    group: input.group ?? "default",
    command: oneline(input.command, 200),
    // Reported so the model can tell the user where it went. If this is not the
    // directory they meant, the task fails for a reason that has nothing to do
    // with the command.
    workingDirectory: resolved ?? `${connection.name}'s home directory`,
    workingDirectoryWasChosen: input.workingDirectory !== undefined,
  };
}
