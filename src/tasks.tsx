/**
 * Browse and act on the queue.
 *
 * Searching is client-side, through per-item `keywords`. The whole state is
 * already in memory and pueue's own query DSL only matches `command` and
 * `label`, so filtering here is both faster and broader. The DSL is still
 * reachable as the command's `query` argument, which is the escape hatch for a
 * queue too large to hold — it runs inside the pueue client, so it shrinks what
 * we parse rather than what the daemon sends.
 */

import { useEffect, useRef, useState } from "react";
import {
  Action,
  ActionPanel,
  Color,
  Icon,
  List,
  getPreferenceValues,
  type LaunchProps,
  Keyboard,
} from "@raycast/api";
import { useCachedPromise, useCachedState } from "@raycast/utils";

import {
  durationAccessory,
  formatRelative,
  formatWhen,
  SECTION_ORDER,
  SECTION_TITLES,
  sectionOf,
  statusColor,
  statusIcon,
  statusTag,
  type SectionKey,
} from "./lib/format";
import { actOnTasks, type ActOptions } from "./lib/actions";
import { canFollow, TaskFollowView, TaskLogView } from "./lib/task-log";
import { ALL_GROUPS, GroupDropdown } from "./lib/group-dropdown";
import {
  ConnectionBannerItem,
  ConnectionSubmenu,
  useConnection,
  type ConnectionState,
} from "./lib/connection-ui";
import {
  describeError,
  ErrorEmptyView,
  StaleBannerItem,
} from "./lib/error-states";
import {
  cleanLogOutput,
  connectionByName,
  endedAt,
  enqueuedAt,
  exitCode,
  groups as readGroups,
  hasEverRun,
  isLocked,
  logs as readLogs,
  oneline,
  parseTs,
  spawnError,
  startedAt,
  status as readStatus,
  statusKeywords,
  taskList,
  taskResult,
  underlyingKind,
  type Mutation,
  type Task,
} from "./lib/pueue";

export default function Command(
  props: LaunchProps<{
    arguments: Arguments.Tasks;
    launchContext?: { group?: string };
  }>,
) {
  const prefs = getPreferenceValues<Preferences.Tasks>();
  const query = props.arguments?.query ?? "";

  // The Groups view launches this command with a group in its context; that
  // choice must win over the remembered filter, or "Show Tasks in Group" would
  // silently show a different group.
  const [group, setGroup] = useCachedState(
    "tasks.group",
    props.launchContext?.group ?? ALL_GROUPS,
  );
  const contextGroup = props.launchContext?.group;
  useEffect(() => {
    if (contextGroup) setGroup(contextGroup);
  }, [contextGroup]);
  const [showDetail, setShowDetail] = useCachedState(
    "tasks.showDetail",
    prefs.showDetail,
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const conn = useConnection();

  // Separate controllers: a superseded state read must not cancel an in-flight
  // group read, and vice versa.
  const stateAbort = useRef<AbortController>(null);
  const groupsAbort = useRef<AbortController>(null);

  const state = useCachedPromise(
    // The connection is an argument rather than a closure capture so that
    // useCachedPromise keys its cache on it — switching daemons must not show
    // the previous one's tasks.
    (g: string, q: string, connectionName: string) =>
      readStatus({
        group: g === ALL_GROUPS ? undefined : g,
        query: q,
        signal: stateAbort.current?.signal,
        connection: connectionByName(connectionName),
      }),
    [group, query, conn.connection.name],
    { keepPreviousData: true, abortable: stateAbort },
  );

  const groupState = useCachedPromise(
    (connectionName: string) =>
      readGroups({
        signal: groupsAbort.current?.signal,
        connection: connectionByName(connectionName),
      }),
    [conn.connection.name],
    {
      keepPreviousData: true,
      abortable: groupsAbort,
    },
  );

  const logLines = Math.max(1, Number(prefs.detailLogLines) || 20);

  const selectedTask = selectedId ? state.data?.tasks[selectedId] : undefined;
  // A task that never started has no log file, and asking anyway does not fail
  // — pueue exits 0 with its own error text in the output field. Skip the call.
  const canHaveLog = selectedTask !== undefined && hasEverRun(selectedTask);

  // Only the selected row, and only when the pane is open — one `pueue log`
  // per selection change is fine, one per row would not be.
  const preview = useCachedPromise(
    async (id: string | null, connectionName: string) => {
      if (!id) return undefined;
      const map = await readLogs([Number(id)], {
        lines: logLines,
        connection: connectionByName(connectionName),
      });
      return cleanLogOutput(map[id]?.output);
    },
    [selectedId, conn.connection.name],
    { execute: showDetail && canHaveLog, keepPreviousData: false },
  );

  const error = state.error ?? groupState.error;
  const isLoading = state.isLoading || groupState.isLoading;
  const tasks = taskList(state.data?.tasks ?? {});

  const bySection = new Map<SectionKey, Task[]>();
  for (const task of tasks) {
    const key = sectionOf(task);
    const bucket = bySection.get(key);
    if (bucket) bucket.push(task);
    else bySection.set(key, [task]);
  }

  const reload = () => {
    state.revalidate();
    groupState.revalidate();
  };

  // A failed first read has nothing to show alongside the error, so the error
  // takes the whole screen.
  if (error && !state.data) {
    return (
      <List searchBarPlaceholder="Search tasks…">
        <ErrorEmptyView
          error={error}
          onRetry={reload}
          connection={conn.connection}
        />
      </List>
    );
  }

  // With cached data we keep rendering it — but a structural failure means that
  // data is not merely a moment old, it is a snapshot of a queue we can no
  // longer see. Say so in the list itself rather than trusting a toast.
  const stale =
    error !== undefined && describeError(error, conn.connection).structural;

  return (
    <List
      isLoading={isLoading}
      isShowingDetail={showDetail && tasks.length > 0}
      onSelectionChange={setSelectedId}
      searchBarPlaceholder={`Search ${tasks.length} task${tasks.length === 1 ? "" : "s"}…`}
      searchBarAccessory={
        <GroupDropdown
          groups={groupState.data}
          value={group}
          onChange={setGroup}
        />
      }
    >
      {stale || conn.connection.remote ? (
        <List.Section title="Connection">
          {stale ? (
            <StaleBannerItem
              error={error}
              onRetry={reload}
              connection={conn.connection}
            />
          ) : null}
          <ConnectionBannerItem state={conn} />
        </List.Section>
      ) : null}
      {tasks.length === 0 ? (
        <List.EmptyView
          icon={Icon.Tray}
          title={query ? "No tasks match that query" : "No tasks"}
          description={
            query
              ? `The query argument is still applied: ${query}`
              : "Nothing in the queue. Add one with the Add Task command."
          }
          actions={
            <ActionPanel>
              <Action
                title="Reload"
                icon={Icon.ArrowClockwise}
                shortcut={Keyboard.Shortcut.Common.Refresh}
                onAction={reload}
              />
            </ActionPanel>
          }
        />
      ) : (
        SECTION_ORDER.filter((key) => bySection.has(key)).map((key) => {
          const items = bySection.get(key) ?? [];
          return (
            <List.Section
              key={key}
              title={SECTION_TITLES[key]}
              subtitle={String(items.length)}
            >
              {items.map((task) => (
                <TaskItem
                  key={task.id}
                  task={task}
                  showDetail={showDetail}
                  logText={
                    String(selectedId) === String(task.id)
                      ? preview.data
                      : undefined
                  }
                  logLoading={preview.isLoading && canHaveLog}
                  logLines={logLines}
                  onToggleDetail={() => setShowDetail((v) => !v)}
                  onReload={reload}
                  onAct={(mutation, options) =>
                    actOnTasks(mutation, state, {
                      ...options,
                      connection: conn.connection,
                    })
                  }
                  connection={conn}
                />
              ))}
            </List.Section>
          );
        })
      )}
    </List>
  );
}

function TaskItem(props: {
  task: Task;
  showDetail: boolean;
  logText: string | undefined;
  logLoading: boolean;
  logLines: number;
  onToggleDetail: () => void;
  onReload: () => void;
  onAct: (mutation: Mutation, options: ActOptions) => Promise<boolean>;
  connection: ConnectionState;
}) {
  const { task, onAct } = props;
  const duration = durationAccessory(task);
  const kind = underlyingKind(task.status);
  const label = `task ${task.id}`;

  // With the detail pane open the row is narrow, so it carries only the status
  // tag; the numbers move into the pane.
  const accessories: List.Item.Accessory[] = props.showDetail
    ? [{ tag: { value: statusTag(task), color: statusColor(task) } }]
    : [
        ...(task.label
          ? [{ tag: { value: task.label, color: Color.SecondaryText } }]
          : []),
        ...(task.group !== "default" ? [{ text: task.group }] : []),
        ...(duration ? [{ text: duration, icon: Icon.Clock }] : []),
        { tag: { value: statusTag(task), color: statusColor(task) } },
      ];

  const canKill = kind === "running" || kind === "paused";
  const canRestart = kind === "done";
  const canStash = kind === "queued";
  const canEnqueue = kind === "stashed";
  const canPause = kind === "running";
  const canResume =
    kind === "paused" || kind === "stashed" || kind === "queued";

  return (
    <List.Item
      id={String(task.id)}
      icon={statusIcon(task)}
      title={oneline(task.command, 90)}
      subtitle={props.showDetail ? undefined : `#${task.id}`}
      keywords={statusKeywords(task)}
      accessories={accessories}
      detail={props.showDetail ? <TaskDetail {...props} /> : undefined}
      actions={
        <ActionPanel>
          <ActionPanel.Section>
            {hasEverRun(task) ? (
              <Action.Push
                title="Show Log"
                icon={Icon.Text}
                target={
                  <TaskLogView
                    task={task}
                    connection={props.connection.connection}
                  />
                }
              />
            ) : null}
            {canFollow(task) ? (
              <Action.Push
                title="Follow Output"
                icon={Icon.Livestream}
                shortcut={{ modifiers: ["cmd"], key: "l" }}
                target={
                  <TaskFollowView
                    task={task}
                    connection={props.connection.connection}
                  />
                }
              />
            ) : null}
            <Action.CopyToClipboard
              title="Copy Command"
              content={task.command}
            />
            <Action.CopyToClipboard
              title="Copy Task ID"
              content={String(task.id)}
              shortcut={Keyboard.Shortcut.Common.Copy}
            />
            <Action.ShowInFinder
              title="Open Working Directory"
              path={task.path}
              shortcut={Keyboard.Shortcut.Common.Open}
            />
          </ActionPanel.Section>

          <ActionPanel.Section title="Control">
            {canRestart ? (
              <Action
                title="Restart (New Task)"
                icon={Icon.Redo}
                shortcut={{ modifiers: ["cmd", "shift"], key: "r" }}
                onAction={() =>
                  onAct(
                    { op: "restart", ids: [task.id] },
                    { verb: `Restarting ${label}`, done: `Restarted ${label}` },
                  )
                }
              />
            ) : null}
            {canRestart ? (
              <Action
                // Reusing the id means the old log is gone, which is exactly
                // what you don't want if you were about to read it.
                title="Restart in Place (Same ID, Overwrites Log)"
                icon={Icon.Repeat}
                shortcut={{ modifiers: ["cmd", "opt"], key: "r" }}
                onAction={() =>
                  onAct(
                    { op: "restart", ids: [task.id], inPlace: true },
                    {
                      verb: `Restarting ${label} in place`,
                      done: `Restarted ${label}`,
                      confirm: {
                        title: `Restart ${label} in place?`,
                        message:
                          "The task keeps its id and its existing output is overwritten.",
                        actionTitle: "Restart in Place",
                        rememberChoice: true,
                      },
                    },
                  )
                }
              />
            ) : null}
            {canPause ? (
              <Action
                title="Pause"
                icon={Icon.Pause}
                shortcut={{ modifiers: ["cmd", "shift"], key: "p" }}
                onAction={() =>
                  onAct(
                    { op: "pause", ids: [task.id] },
                    { verb: `Pausing ${label}`, done: `Paused ${label}` },
                  )
                }
              />
            ) : null}
            {canResume ? (
              <Action
                title={kind === "paused" ? "Resume" : "Start Now"}
                icon={Icon.Play}
                shortcut={Keyboard.Shortcut.Common.Duplicate}
                onAction={() =>
                  onAct(
                    { op: "start", ids: [task.id] },
                    { verb: `Starting ${label}`, done: `Started ${label}` },
                  )
                }
              />
            ) : null}
            {canStash ? (
              <Action
                title="Stash"
                icon={Icon.Tray}
                shortcut={{ modifiers: ["cmd", "shift"], key: "t" }}
                onAction={() =>
                  onAct(
                    { op: "stash", ids: [task.id] },
                    { verb: `Stashing ${label}`, done: `Stashed ${label}` },
                  )
                }
              />
            ) : null}
            {canEnqueue ? (
              <Action
                title="Enqueue"
                icon={Icon.Clock}
                shortcut={{ modifiers: ["cmd", "shift"], key: "e" }}
                onAction={() =>
                  onAct(
                    { op: "enqueue", ids: [task.id] },
                    { verb: `Enqueueing ${label}`, done: `Enqueued ${label}` },
                  )
                }
              />
            ) : null}
            {canKill ? (
              <Action
                title="Kill"
                icon={Icon.Stop}
                style={Action.Style.Destructive}
                shortcut={{ modifiers: ["cmd", "shift"], key: "k" }}
                onAction={() =>
                  onAct(
                    { op: "kill", ids: [task.id] },
                    {
                      verb: `Killing ${label}`,
                      done: `Killed ${label}`,
                      confirm: {
                        title: `Kill ${label}?`,
                        message: oneline(task.command, 120),
                        actionTitle: "Kill",
                        destructive: true,
                        rememberChoice: true,
                      },
                    },
                  )
                }
              />
            ) : null}
            <Action
              // pueue refuses to remove a running or paused task. Rather than
              // hide the action and leave the user guessing, keep it and let
              // pueue's own refusal explain why.
              title="Remove"
              icon={Icon.Trash}
              style={Action.Style.Destructive}
              shortcut={Keyboard.Shortcut.Common.Remove}
              onAction={() =>
                onAct(
                  { op: "remove", ids: [task.id] },
                  {
                    verb: `Removing ${label}`,
                    done: `Removed ${label}`,
                    confirm: {
                      title: `Remove ${label}?`,
                      message: canKill
                        ? "pueue will refuse this while the task is running — kill it first."
                        : oneline(task.command, 120),
                      actionTitle: "Remove",
                      destructive: true,
                    },
                  },
                )
              }
            />
          </ActionPanel.Section>

          <ActionPanel.Section>
            <Action
              title={props.showDetail ? "Hide Detail" : "Show Detail"}
              icon={Icon.Sidebar}
              shortcut={{ modifiers: ["cmd", "shift"], key: "d" }}
              onAction={props.onToggleDetail}
            />
            <Action
              title="Clean Finished Tasks"
              icon={Icon.Trash}
              style={Action.Style.Destructive}
              onAction={() =>
                onAct(
                  { op: "clean" },
                  {
                    verb: "Cleaning finished tasks",
                    done: "Cleaned finished tasks",
                    confirm: {
                      title: "Remove every finished task?",
                      message:
                        "Successful and failed tasks are both discarded, with their logs.",
                      actionTitle: "Clean",
                      destructive: true,
                    },
                  },
                )
              }
            />
            <Action
              title="Reload"
              icon={Icon.ArrowClockwise}
              shortcut={Keyboard.Shortcut.Common.Refresh}
              onAction={props.onReload}
            />
            <ConnectionSubmenu state={props.connection} />
          </ActionPanel.Section>
        </ActionPanel>
      }
    />
  );
}

function TaskDetail(props: {
  task: Task;
  logText: string | undefined;
  logLoading: boolean;
  logLines: number;
}) {
  const { task } = props;
  const result = taskResult(task.status);
  const code = exitCode(result);
  const spawn = spawnError(result);

  const started = startedAt(task.status);
  const ended = endedAt(task.status);
  const enqueued = enqueuedAt(task.status);
  const created = parseTs(task.created_at);
  const kind = underlyingKind(task.status);

  const body = props.logText ?? "";
  const markdown = [
    "```sh",
    task.command.replace(/```/g, "``​`"),
    "```",
    "",
    `**Output** · last ${props.logLines} lines`,
    "",
    body
      ? ["```text", body.replace(/```/g, "``​`"), "```"].join("\n")
      : props.logLoading
        ? "_Loading…_"
        : hasEverRun(task)
          ? "_No output._"
          : "_This task hasn't run yet._",
  ].join("\n");

  return (
    <List.Item.Detail
      markdown={markdown}
      metadata={
        <List.Item.Detail.Metadata>
          <List.Item.Detail.Metadata.Label title="Task" text={`#${task.id}`} />
          <List.Item.Detail.Metadata.TagList title="Status">
            <List.Item.Detail.Metadata.TagList.Item
              text={statusTag(task)}
              color={statusColor(task)}
            />
            {isLocked(task.status) ? (
              <List.Item.Detail.Metadata.TagList.Item
                text="Editing"
                color={Color.Purple}
              />
            ) : null}
          </List.Item.Detail.Metadata.TagList>
          {spawn ? (
            <List.Item.Detail.Metadata.Label title="Spawn error" text={spawn} />
          ) : null}
          {code !== undefined ? (
            <List.Item.Detail.Metadata.Label
              title="Exit code"
              text={String(code)}
            />
          ) : null}
          <List.Item.Detail.Metadata.Separator />
          <List.Item.Detail.Metadata.Label title="Group" text={task.group} />
          {task.label ? (
            <List.Item.Detail.Metadata.Label title="Label" text={task.label} />
          ) : null}
          {task.priority !== 0 ? (
            <List.Item.Detail.Metadata.Label
              title="Priority"
              text={String(task.priority)}
            />
          ) : null}
          {task.dependencies.length > 0 ? (
            <List.Item.Detail.Metadata.Label
              title="Depends on"
              text={task.dependencies.map((d) => `#${d}`).join(", ")}
            />
          ) : null}
          <List.Item.Detail.Metadata.Label title="Directory" text={task.path} />
          <List.Item.Detail.Metadata.Separator />
          {created ? (
            <List.Item.Detail.Metadata.Label
              title="Created"
              text={formatWhen(created)}
              icon={Icon.Calendar}
            />
          ) : null}
          {/* A stashed task's timestamp points *forward* — it is when the task
              will be enqueued, not when it was. Label it accordingly. */}
          {enqueued ? (
            <List.Item.Detail.Metadata.Label
              title={kind === "stashed" ? "Scheduled for" : "Enqueued"}
              text={`${formatWhen(enqueued)} · ${formatRelative(enqueued)}`}
            />
          ) : null}
          {started ? (
            <List.Item.Detail.Metadata.Label
              title="Started"
              text={`${formatWhen(started)} · ${formatRelative(started)}`}
            />
          ) : null}
          {ended ? (
            <List.Item.Detail.Metadata.Label
              title="Ended"
              text={formatWhen(ended)}
            />
          ) : null}
          {durationAccessory(task) ? (
            <List.Item.Detail.Metadata.Label
              title={ended ? "Duration" : "Running for"}
              text={durationAccessory(task)}
            />
          ) : null}
        </List.Item.Detail.Metadata>
      }
    />
  );
}
