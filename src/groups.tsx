/**
 * Groups: parallelism limits and pause state, one row each.
 *
 * Two of pueue's group operations do something the name doesn't say, and both
 * confirmations spell it out — `kill --group` also *pauses* the group, and
 * `group remove` *moves* the group's tasks to `default` rather than deleting
 * them. Both were read from `pueue --help`, not assumed.
 */

import { useRef, useState } from "react";
import {
  Action,
  ActionPanel,
  Color,
  Form,
  Icon,
  Keyboard,
  LaunchType,
  List,
  launchCommand,
  useNavigation,
} from "@raycast/api";
import { useCachedPromise } from "@raycast/utils";

import { actOnGroups, type ActOptions } from "./lib/actions";
import {
  describeError,
  ErrorEmptyView,
  StaleBannerItem,
} from "./lib/error-states";
import { groupIcon } from "./lib/format";
import {
  ConnectionBannerItem,
  ConnectionSubmenu,
  useConnection,
  type ConnectionState,
} from "./lib/connection-ui";
import {
  connectionByName,
  groups as readGroups,
  isPaused,
  isQueued,
  isRunning,
  status as readStatus,
  taskList,
  type Group,
  type Mutation,
} from "./lib/pueue";

/** Offered in the parallelism submenu. 0 is pueue's "unlimited". */
const PARALLEL_CHOICES = [1, 2, 3, 4, 6, 8, 12, 0];

export default function Command() {
  const conn = useConnection();
  const groupsAbort = useRef<AbortController>(null);
  const stateAbort = useRef<AbortController>(null);

  // `group --json` is authoritative for status and parallelism; `status --json`
  // supplies the per-group task counts. Separate controllers so a superseded
  // read of one can't cancel the other.
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
  const state = useCachedPromise(
    (connectionName: string) =>
      readStatus({
        signal: stateAbort.current?.signal,
        connection: connectionByName(connectionName),
      }),
    [conn.connection.name],
    {
      keepPreviousData: true,
      abortable: stateAbort,
    },
  );

  const error = groupState.error ?? state.error;
  const reload = () => {
    groupState.revalidate();
    state.revalidate();
  };

  if (error && !groupState.data) {
    return (
      <List searchBarPlaceholder="Search groups…">
        <ErrorEmptyView
          error={error}
          onRetry={reload}
          connection={conn.connection}
        />
      </List>
    );
  }

  const stale =
    error !== undefined && describeError(error, conn.connection).structural;
  const tasks = taskList(state.data?.tasks ?? {});
  const entries = Object.entries(groupState.data ?? {}).sort(([a], [b]) =>
    a.localeCompare(b),
  );

  return (
    <List
      isLoading={groupState.isLoading || state.isLoading}
      searchBarPlaceholder={`Search ${entries.length} group${entries.length === 1 ? "" : "s"}…`}
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

      {entries.length === 0 ? (
        <List.EmptyView icon={Icon.Tray} title="No groups" />
      ) : (
        entries.map(([name, group]) => (
          <GroupItem
            key={name}
            name={name}
            group={group}
            running={
              tasks.filter((t) => t.group === name && isRunning(t)).length
            }
            paused={tasks.filter((t) => t.group === name && isPaused(t)).length}
            queued={tasks.filter((t) => t.group === name && isQueued(t)).length}
            total={tasks.filter((t) => t.group === name).length}
            onReload={reload}
            onAct={(mutation, options) =>
              actOnGroups(mutation, groupState, {
                ...options,
                connection: conn.connection,
              })
            }
            connection={conn}
          />
        ))
      )}
    </List>
  );
}

function GroupItem(props: {
  name: string;
  group: Group;
  running: number;
  paused: number;
  queued: number;
  total: number;
  onReload: () => void;
  onAct: (mutation: Mutation, options: ActOptions) => Promise<boolean>;
  connection: ConnectionState;
}) {
  const { push } = useNavigation();
  const { name, group, onAct } = props;
  const isDefault = name === "default";
  const limit = group.parallel_tasks === 0 ? "∞" : String(group.parallel_tasks);

  return (
    <List.Item
      icon={groupIcon(group)}
      title={name}
      subtitle={`${props.running}/${limit} running`}
      keywords={[name, group.status.toLowerCase()]}
      accessories={[
        ...(props.queued > 0 ? [{ text: `${props.queued} queued` }] : []),
        ...(props.paused > 0 ? [{ text: `${props.paused} paused` }] : []),
        {
          tag: {
            value: group.status,
            color:
              group.status === "Running"
                ? Color.Green
                : group.status === "Paused"
                  ? Color.Orange
                  : Color.SecondaryText,
          },
        },
      ]}
      actions={
        <ActionPanel>
          <ActionPanel.Section>
            <Action
              title="Show Tasks in Group"
              icon={Icon.AppWindowList}
              onAction={() =>
                launchCommand({
                  name: "tasks",
                  type: LaunchType.UserInitiated,
                  arguments: { query: "" },
                  context: { group: name },
                }).catch(() => {})
              }
            />
            {group.status === "Running" ? (
              <Action
                title="Pause Group"
                icon={Icon.Pause}
                shortcut={{ modifiers: ["cmd", "shift"], key: "p" }}
                onAction={() =>
                  onAct(
                    { op: "pause", group: name },
                    { verb: `Pausing ${name}`, done: `Paused ${name}` },
                  )
                }
              />
            ) : (
              <Action
                title="Resume Group"
                icon={Icon.Play}
                shortcut={Keyboard.Shortcut.Common.Duplicate}
                onAction={() =>
                  onAct(
                    { op: "start", group: name },
                    { verb: `Resuming ${name}`, done: `Resumed ${name}` },
                  )
                }
              />
            )}
            {group.status === "Running" ? (
              <Action
                title="Pause Group (Let Running Tasks Finish)"
                icon={Icon.Pause}
                onAction={() =>
                  onAct(
                    { op: "pause", group: name, wait: true },
                    {
                      verb: `Pausing ${name}`,
                      done: `Pausing ${name} after current tasks`,
                    },
                  )
                }
              />
            ) : null}
          </ActionPanel.Section>

          <ActionPanel.Section title="Parallelism">
            <ActionPanel.Submenu
              title="Set Parallelism"
              icon={Icon.BulletPoints}
              shortcut={{ modifiers: ["cmd", "shift"], key: "l" }}
            >
              {PARALLEL_CHOICES.map((n) => (
                <Action
                  key={n}
                  title={n === 0 ? "Unlimited" : String(n)}
                  icon={
                    n === group.parallel_tasks ? Icon.Checkmark : Icon.Circle
                  }
                  onAction={() =>
                    onAct(
                      { op: "parallel", count: n, group: name },
                      {
                        verb: `Setting ${name} parallelism`,
                        done: `${name} runs ${n === 0 ? "unlimited" : n} at a time`,
                      },
                    )
                  }
                />
              ))}
            </ActionPanel.Submenu>
          </ActionPanel.Section>

          <ActionPanel.Section title="Manage">
            <Action
              title="Add Group…"
              icon={Icon.Plus}
              shortcut={Keyboard.Shortcut.Common.New}
              onAction={() =>
                push(<AddGroupForm onAct={onAct} onDone={props.onReload} />)
              }
            />
            <Action
              title="Kill Running Tasks in Group"
              icon={Icon.Stop}
              style={Action.Style.Destructive}
              shortcut={{ modifiers: ["cmd", "shift"], key: "k" }}
              onAction={() =>
                onAct(
                  { op: "kill", group: name },
                  {
                    verb: `Killing tasks in ${name}`,
                    done: `Killed running tasks in ${name}`,
                    confirm: {
                      title: `Kill running tasks in “${name}”?`,
                      // Verified in `pueue kill --help`: "Kill all running tasks
                      // in a group. This also pauses the group".
                      message: `${props.running} running task${props.running === 1 ? "" : "s"} will be killed, and the group will also be paused.`,
                      actionTitle: "Kill and Pause",
                      destructive: true,
                    },
                  },
                )
              }
            />
            {!isDefault ? (
              <Action
                title="Remove Group"
                icon={Icon.Trash}
                style={Action.Style.Destructive}
                shortcut={Keyboard.Shortcut.Common.Remove}
                onAction={() =>
                  onAct(
                    { op: "group-remove", name },
                    {
                      verb: `Removing ${name}`,
                      done: `Removed ${name}`,
                      confirm: {
                        title: `Remove the group “${name}”?`,
                        // Verified in `pueue group remove --help`: "This will
                        // move all tasks in this group to the default group!"
                        message:
                          props.total > 0
                            ? `Its ${props.total} task${props.total === 1 ? "" : "s"} will be moved to the default group, not deleted.`
                            : "The group is empty.",
                        actionTitle: "Remove Group",
                        destructive: true,
                      },
                    },
                  )
                }
              />
            ) : null}
            <Action
              title="Reset Group"
              icon={Icon.ExclamationMark}
              style={Action.Style.Destructive}
              onAction={() =>
                onAct(
                  { op: "reset", groups: [name] },
                  {
                    verb: `Resetting ${name}`,
                    done: `Reset ${name}`,
                    confirm: {
                      title: `Reset “${name}”?`,
                      message:
                        "Every task in this group is killed and removed, along with its logs. This cannot be undone.",
                      actionTitle: "Reset",
                      destructive: true,
                      // No rememberChoice: this one should ask every time.
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

function AddGroupForm(props: {
  onAct: (mutation: Mutation, options: ActOptions) => Promise<boolean>;
  onDone: () => void;
}) {
  const { pop } = useNavigation();
  const [name, setName] = useState("");
  const [parallel, setParallel] = useState("1");
  const [nameError, setNameError] = useState<string | undefined>();

  return (
    <Form
      actions={
        <ActionPanel>
          <Action.SubmitForm
            title="Add Group"
            icon={Icon.Plus}
            onSubmit={async () => {
              const trimmed = name.trim();
              if (!trimmed) {
                setNameError("Required");
                return;
              }
              const ok = await props.onAct(
                {
                  op: "group-add",
                  name: trimmed,
                  parallel: Number(parallel) || undefined,
                },
                { verb: `Adding ${trimmed}`, done: `Added group ${trimmed}` },
              );
              if (ok) {
                props.onDone();
                pop();
              }
            }}
          />
        </ActionPanel>
      }
    >
      <Form.TextField
        id="name"
        title="Name"
        placeholder="gpu"
        autoFocus
        value={name}
        error={nameError}
        onChange={(v) => {
          setName(v);
          if (nameError) setNameError(undefined);
        }}
      />
      <Form.Dropdown
        id="parallel"
        title="Parallel Tasks"
        value={parallel}
        onChange={setParallel}
        info="How many tasks this group runs at once. Unlimited means pueue never holds one back."
      >
        {PARALLEL_CHOICES.map((n) => (
          <Form.Dropdown.Item
            key={n}
            value={String(n)}
            title={n === 0 ? "Unlimited" : String(n)}
          />
        ))}
      </Form.Dropdown>
    </Form>
  );
}
