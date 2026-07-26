/**
 * Queue a command.
 *
 * The escaping discipline matters more here than anywhere else in the
 * extension: the text in this box is a shell command line, and pueue runs it
 * through `sh -c` itself. It is passed as a single argv element after `--` and
 * is never quoted, joined, or interpolated on the way — see `lib/pueue/argv.ts`.
 */

import { useEffect, useState } from "react";
import {
  Action,
  ActionPanel,
  Form,
  Icon,
  LaunchType,
  LocalStorage,
  Toast,
  launchCommand,
  popToRoot,
  showToast,
  useNavigation,
  type LaunchProps,
} from "@raycast/api";
import { useCachedPromise } from "@raycast/utils";
import { homedir } from "node:os";

import { ErrorDetail } from "./lib/error-states";
import { connectionIcon, useConnection } from "./lib/connection-ui";
import {
  connectionByName,
  groups as readGroups,
  isBinaryMissing,
  isDaemonDown,
  mutate,
  oneline,
  status as readStatus,
  taskList,
  underlyingKind,
  firstLine,
  PueueError,
} from "./lib/pueue";

const LAST_GROUP = "add.group";
const LAST_CWD = "add.cwd";

/** How a task enters the queue. One control, because the CLI flags are exclusive. */
type StartMode = "queued" | "stashed" | "immediate";

export default function Command(
  props: LaunchProps<{ arguments: Arguments.AddTask }>,
) {
  const { pop } = useNavigation();
  const conn = useConnection();
  const remote = conn.connection.remote;
  const [command, setCommand] = useState(props.arguments?.command ?? "");
  // A remote path can't be browsed to, so it is typed rather than picked.
  const [remoteDirectory, setRemoteDirectory] = useState("");
  const [group, setGroup] = useState<string>("default");
  const [workingDirectory, setWorkingDirectory] = useState<string[]>([]);
  const [label, setLabel] = useState("");
  const [priority, setPriority] = useState("");
  const [delay, setDelay] = useState("");
  const [dependencies, setDependencies] = useState<string[]>([]);
  const [startMode, setStartMode] = useState<StartMode>("queued");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [priorityError, setPriorityError] = useState<string | undefined>();
  const [submitting, setSubmitting] = useState(false);

  const groupState = useCachedPromise(
    (connectionName: string) =>
      readGroups({ connection: connectionByName(connectionName) }),
    [conn.connection.name],
    { keepPreviousData: true },
  );
  const state = useCachedPromise(
    (connectionName: string) =>
      readStatus({ connection: connectionByName(connectionName) }),
    [conn.connection.name],
    { keepPreviousData: true },
  );

  // Seed the group and directory from the last submission, so the common case
  // of "same project, same queue" is one keystroke.
  useEffect(() => {
    LocalStorage.getItem<string>(LAST_GROUP).then((g) => {
      if (g) setGroup(g);
    });
    LocalStorage.getItem<string>(LAST_CWD).then((d) => {
      setWorkingDirectory([d && d.length > 0 ? d : homedir()]);
    });
  }, []);

  // Only unfinished tasks can be depended on; depending on a finished one is
  // either a no-op or an instant DependencyFailed.
  const dependencyOptions = taskList(state.data?.tasks ?? {}).filter((task) =>
    ["queued", "stashed", "running", "paused"].includes(
      underlyingKind(task.status),
    ),
  );

  const groupNames = Object.keys(groupState.data ?? {});
  const knownGroups = groupNames.length > 0 ? groupNames.sort() : ["default"];

  const fatal = groupState.error ?? state.error;
  if (fatal && (isBinaryMissing(fatal) || isDaemonDown(fatal))) {
    return (
      <ErrorDetail
        error={fatal}
        onRetry={() => {
          groupState.revalidate();
          state.revalidate();
        }}
      />
    );
  }

  async function submit() {
    const trimmed = command.trim();
    if (!trimmed) {
      await showToast({
        style: Toast.Style.Failure,
        title: "Nothing to queue",
      });
      return;
    }
    if (priority && !/^-?\d+$/.test(priority.trim())) {
      setPriorityError("Must be a whole number");
      return;
    }

    setSubmitting(true);
    const toast = await showToast({
      style: Toast.Style.Animated,
      title: "Queueing…",
    });
    try {
      const id = await mutate(
        {
          op: "add",
          command: trimmed,
          group: group === "default" ? undefined : group,
          label: label.trim() || undefined,
          priority: priority.trim() ? Number(priority.trim()) : undefined,
          workingDirectory: remote
            ? remoteDirectory.trim() || undefined
            : workingDirectory[0],
          after: dependencies.map(Number),
          delay: delay.trim() || undefined,
          stashed: startMode === "stashed",
          immediate: startMode === "immediate",
        },
        { connection: conn.connection },
      );

      toast.style = Toast.Style.Success;
      toast.title = id === undefined ? "Queued" : `Queued task ${id}`;
      toast.message = oneline(trimmed, 60);

      await LocalStorage.setItem(LAST_GROUP, group);
      if (workingDirectory[0])
        await LocalStorage.setItem(LAST_CWD, workingDirectory[0]);

      // Documented use of launchCommand: force a sibling's background refresh,
      // so the menu bar catches up in seconds rather than at its next interval.
      // Resolves when launched, not when finished, so it must not be awaited
      // for correctness — and a disabled menu bar command is not an error here.
      launchCommand({ name: "queue-menu", type: LaunchType.Background }).catch(
        () => {},
      );

      pop();
      await popToRoot();
    } catch (error) {
      toast.style = Toast.Style.Failure;
      toast.title = "Could not queue the task";
      toast.message =
        error instanceof PueueError
          ? firstLine(error.detail)
          : String((error as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Form
      isLoading={submitting || state.isLoading || groupState.isLoading}
      enableDrafts
      actions={
        <ActionPanel>
          <Action.SubmitForm
            title="Queue Task"
            icon={Icon.Plus}
            onSubmit={submit}
          />
          <Action
            title={
              showAdvanced ? "Hide Advanced Options" : "Show Advanced Options"
            }
            icon={Icon.Cog}
            shortcut={{ modifiers: ["cmd", "shift"], key: "a" }}
            onAction={() => setShowAdvanced((v) => !v)}
          />
        </ActionPanel>
      }
    >
      {conn.switchable ? (
        <Form.Dropdown
          id="connection"
          title="Daemon"
          value={conn.connection.name}
          onChange={conn.setName}
          info={
            remote
              ? "Submitted over SSH so the working directory is resolved on the remote host."
              : "This machine's pueue daemon."
          }
        >
          {conn.all.map((c) => (
            <Form.Dropdown.Item
              key={c.name}
              value={c.name}
              title={c.sshHost ? `${c.name} (${c.sshHost})` : c.name}
              icon={connectionIcon(c)}
            />
          ))}
        </Form.Dropdown>
      ) : null}

      <Form.TextArea
        id="command"
        title="Command"
        placeholder="cargo build --release"
        autoFocus
        value={command}
        onChange={setCommand}
        // The trailing-& warning is not pedantry: pueue runs the command with
        // `sh -c`, so a detached process makes the task "finish" immediately
        // while the real work keeps running unsupervised.
        info="Run by the pueue daemon via `sh -c`, so &&, |, and > all work. A trailing & detaches the process and the task finishes instantly."
      />

      <Form.Dropdown id="group" title="Group" value={group} onChange={setGroup}>
        {knownGroups.map((name) => (
          <Form.Dropdown.Item key={name} value={name} title={name} />
        ))}
        {/* Keep a remembered group that has since been deleted selectable,
            rather than letting the dropdown silently reset to default. */}
        {!knownGroups.includes(group) ? (
          <Form.Dropdown.Item value={group} title={`${group} (gone)`} />
        ) : null}
      </Form.Dropdown>

      {remote ? (
        // A picker would browse *this* machine, and pueue canonicalises the
        // working directory on whichever host the client runs on. Since this
        // submission goes over SSH, the path is resolved on the remote box —
        // so it must be typed, and it must exist there.
        <Form.TextField
          id="remoteDirectory"
          title="Working Directory"
          placeholder="/home/you/project"
          value={remoteDirectory}
          onChange={setRemoteDirectory}
          info={
            conn.connection.sshHost
              ? `A path on ${conn.connection.sshHost}. Left empty, the task runs in your SSH login directory.`
              : "This connection has no SSH host, so the path is resolved on THIS machine and almost certainly won't exist on the remote daemon. Add an ssh host to the connection to submit properly."
          }
        />
      ) : (
        <Form.FilePicker
          id="workingDirectory"
          title="Working Directory"
          allowMultipleSelection={false}
          canChooseDirectories
          canChooseFiles={false}
          value={workingDirectory}
          onChange={setWorkingDirectory}
        />
      )}

      <Form.Dropdown
        id="startMode"
        title="Start"
        value={startMode}
        onChange={(v) => setStartMode(v as StartMode)}
        // One control rather than two checkboxes: --stashed and --immediate are
        // mutually exclusive, and two checkboxes would let you tick both.
        info="Queued waits its turn. Stashed sits out until you enqueue it. Immediate ignores the parallelism limit."
      >
        <Form.Dropdown.Item value="queued" title="Queued" icon={Icon.Clock} />
        <Form.Dropdown.Item value="stashed" title="Stashed" icon={Icon.Tray} />
        <Form.Dropdown.Item
          value="immediate"
          title="Start Immediately"
          icon={Icon.Play}
        />
      </Form.Dropdown>

      <Form.TextField
        id="label"
        title="Label"
        placeholder="nightly"
        value={label}
        onChange={setLabel}
      />

      {showAdvanced ? (
        <>
          <Form.Separator />

          <Form.TagPicker
            id="dependencies"
            title="Run After"
            value={dependencies}
            onChange={setDependencies}
            info="This task waits for the selected tasks. If any of them fails, this one is marked DependencyFailed and never runs."
          >
            {dependencyOptions.map((task) => (
              <Form.TagPicker.Item
                key={task.id}
                value={String(task.id)}
                title={`${task.id} · ${oneline(task.command, 40)}`}
              />
            ))}
          </Form.TagPicker>

          <Form.TextField
            id="delay"
            title="Delay"
            placeholder="2h"
            value={delay}
            onChange={setDelay}
            info="Seconds (3600s), a duration (2h, 1week), or a date expression (18:30, 2026-08-01T18:30:00, wednesday 10:30pm)."
          />

          <Form.TextField
            id="priority"
            title="Priority"
            placeholder="0"
            value={priority}
            error={priorityError}
            onChange={(v) => {
              setPriority(v);
              if (priorityError) setPriorityError(undefined);
            }}
            info="Higher runs sooner. Defaults to 0; negatives are allowed."
          />
        </>
      ) : (
        <Form.Description
          title="More"
          text="⌘⇧A for dependencies, delay, and priority."
        />
      )}
    </Form>
  );
}
