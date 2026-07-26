/**
 * Groups, and how far through their work they are.
 *
 * A group used to render as `running (0/1)` — running tasks over parallelism,
 * two numbers that barely move. What a group is actually *for* is a batch: queue
 * twenty jobs into it and watch them land. So the row leads with progress, and
 * the detail pane carries the rest of what `group-summary.ts` computes.
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
  getPreferenceValues,
  launchCommand,
  useNavigation,
} from "@raycast/api";
import { useCachedPromise, useCachedState } from "@raycast/utils";

import { actOnTasks, type ActOptions } from "./lib/actions";
import {
  describeError,
  ErrorEmptyView,
  StaleBannerItem,
} from "./lib/error-states";
import { formatDuration, groupProgressIcon } from "./lib/format";
import {
  parallelLabel,
  progressPercent,
  summarizeAll,
  summarizeGroups,
  summaryLine,
  type GroupSummary,
  type OverallSummary,
} from "./lib/group-summary";
import {
  ConnectionBannerItem,
  ConnectionSubmenu,
  InvalidConnectionItems,
  useConnection,
  type ConnectionState,
} from "./lib/connection-ui";
import {
  connectionByName,
  forConnection,
  snapshot,
  taskList,
  type Mutation,
} from "./lib/pueue";

/**
 * Offered in the parallelism submenu. 0 is pueue's "unlimited".
 *
 * A list rather than a number field because picking 4 should be one keystroke,
 * and it stops at 32 because past that you are better served by the `Custom…`
 * item than by a longer menu. Deliberately not seeded from
 * `os.cpus().length`: for a remote connection that is this machine's core
 * count, not the daemon's, and a plausible wrong number is worse than a static
 * list.
 */
const PARALLEL_CHOICES = [1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 0];

export default function Command() {
  const prefs = getPreferenceValues<Preferences.Groups>();
  const conn = useConnection();
  const stateAbort = useRef<AbortController>(null);
  const [showDetail, setShowDetail] = useCachedState(
    "groups.showDetail",
    prefs.showDetail,
  );

  // One read, not two. `status --json` returns the same groups map that
  // `group --json` does — verified byte-identical against 4.0.4, and a
  // `--group` filter narrows only the tasks, never the map. A second call
  // bought nothing but a spare subprocess and a second cache entry that could
  // disagree with the first.
  const state = useCachedPromise(
    (connectionName: string) =>
      snapshot({
        signal: stateAbort.current?.signal,
        connection: connectionByName(connectionName),
      }),
    [conn.connection.name],
    {
      keepPreviousData: true,
      abortable: stateAbort,
    },
  );

  // `keepPreviousData` serves the last successful read from *any* connection
  // when this one has no cache entry yet, so an unreachable daemon would
  // otherwise show another machine's groups under its name.
  const snap = forConnection(state.data, conn.connection.name);
  const error = state.error;
  const reload = () => state.revalidate();

  if (error && !snap) {
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
  const tasks = taskList(snap?.state.tasks ?? {});
  const summaries = summarizeGroups(snap?.state.groups ?? {}, tasks);
  const overall = summarizeAll(summaries);

  return (
    <List
      isLoading={state.isLoading}
      isShowingDetail={showDetail && summaries.length > 0}
      searchBarPlaceholder={`Search ${summaries.length} group${summaries.length === 1 ? "" : "s"}…`}
    >
      {stale || conn.switchable || conn.invalid.length > 0 ? (
        <List.Section title="Connection">
          {stale ? (
            <StaleBannerItem
              error={error}
              onRetry={reload}
              connection={conn.connection}
            />
          ) : null}
          <ConnectionBannerItem state={conn} />
          <InvalidConnectionItems state={conn} />
        </List.Section>
      ) : null}

      {summaries.length === 0 ? (
        <List.EmptyView
          icon={Icon.Tray}
          title="No groups"
          actions={
            <ActionPanel>
              <ConnectionSubmenu state={conn} />
            </ActionPanel>
          }
        />
      ) : (
        // The whole queue, free, in the heading the rows already sit under.
        <List.Section title="Groups" subtitle={overallLine(overall)}>
          {summaries.map((summary) => (
            <GroupItem
              key={summary.name}
              summary={summary}
              showDetail={showDetail}
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
      )}
    </List>
  );
}

/** `13/26 done · 6 failed` — omitted entirely when there is nothing to say. */
function overallLine(o: OverallSummary): string | undefined {
  if (o.total === 0) return undefined;
  const parts = [`${o.finished}/${o.total} done`];
  if (o.running > 0) parts.push(`${o.running} running`);
  if (o.failed > 0) parts.push(`${o.failed} failed`);
  return parts.join(" · ");
}

/**
 * `#4, #5 and #6` — or `#4, #5 and 8 others` once naming them all stops helping.
 *
 * A confirmation that says "6 tasks" and one that says which six are different
 * things to agree to.
 */
function failedIdList(ids: readonly number[], max = 6): string {
  const shown = ids.slice(0, max).map((id) => `#${id}`);
  const rest = ids.length - shown.length;
  if (rest > 0) return `${shown.join(", ")} and ${rest} more`;
  if (shown.length === 1) return shown[0];
  return `${shown.slice(0, -1).join(", ")} and ${shown[shown.length - 1]}`;
}

function GroupItem(props: {
  summary: GroupSummary;
  showDetail: boolean;
  onToggleDetail: () => void;
  onReload: () => void;
  onAct: (mutation: Mutation, options: ActOptions) => Promise<boolean>;
  connection: ConnectionState;
}) {
  const { push } = useNavigation();
  const { summary: s, onAct } = props;
  const name = s.name;
  const isDefault = name === "default";
  const eta = formatDuration(s.etaMs);

  return (
    <List.Item
      icon={groupProgressIcon(s)}
      title={name}
      // With the pane open the row is a third of the window; the numbers move
      // into the pane and the row keeps only what identifies the group.
      subtitle={props.showDetail ? undefined : summaryLine(s)}
      keywords={[
        name,
        s.status.toLowerCase(),
        ...(s.failed > 0 ? ["failed"] : []),
        ...(s.running > 0 ? ["running"] : []),
      ]}
      accessories={[
        ...(!props.showDetail && eta ? [{ text: `~${eta} left` }] : []),
        {
          tag: {
            value: s.status,
            color:
              s.status === "Running"
                ? Color.Green
                : s.status === "Paused"
                  ? Color.Orange
                  : Color.SecondaryText,
          },
        },
      ]}
      detail={props.showDetail ? <GroupDetail summary={s} /> : undefined}
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
            {s.status === "Running" ? (
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
            {s.status === "Running" ? (
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
                  icon={n === s.parallel ? Icon.Checkmark : Icon.Circle}
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
              {/* A preset list is a compromise, and on a machine with enough
                  cores it is the wrong one. The current value appears here too
                  when it is off-list, so the submenu never looks like it has
                  silently lost your setting. */}
              <Action
                title={
                  PARALLEL_CHOICES.includes(s.parallel)
                    ? "Custom…"
                    : `Custom… (currently ${parallelLabel(s.parallel)})`
                }
                icon={Icon.Pencil}
                onAction={() =>
                  push(
                    <ParallelismForm
                      name={name}
                      current={s.parallel}
                      onAct={onAct}
                    />,
                  )
                }
              />
            </ActionPanel.Submenu>
          </ActionPanel.Section>

          <ActionPanel.Section title="Batch">
            {/* Only offered when there is something to restart: pueue accepts
                `--failed-in-group` on a group with no failures and exits 0
                without a word, so an always-visible action would silently do
                nothing. Verified against 4.0.4. */}
            {s.failed > 0 ? (
              <Action
                title={`Restart ${s.failed} Failed (New Tasks)`}
                icon={Icon.Redo}
                shortcut={{ modifiers: ["cmd", "shift"], key: "r" }}
                onAction={() =>
                  onAct(
                    { op: "restart", failedInGroup: name },
                    {
                      verb: `Restarting failures in ${name}`,
                      done: `Restarted ${s.failed} task${s.failed === 1 ? "" : "s"} in ${name}`,
                      confirm: {
                        title: `Restart ${s.failed} failed task${s.failed === 1 ? "" : "s"} in “${name}”?`,
                        message: `${failedIdList(s.failedIds)} will be queued again as new tasks. The originals keep their logs.`,
                        actionTitle: "Restart",
                        rememberChoice: true,
                      },
                    },
                  )
                }
              />
            ) : null}
            {s.failed > 0 ? (
              <Action
                title={`Restart ${s.failed} Failed in Place (Overwrites Logs)`}
                icon={Icon.Repeat}
                shortcut={{ modifiers: ["cmd", "opt"], key: "r" }}
                onAction={() =>
                  onAct(
                    { op: "restart", failedInGroup: name, inPlace: true },
                    {
                      verb: `Restarting failures in ${name}`,
                      done: `Restarted ${s.failed} task${s.failed === 1 ? "" : "s"} in place`,
                      confirm: {
                        title: `Restart ${s.failed} failed task${s.failed === 1 ? "" : "s"} in place?`,
                        message: `${failedIdList(s.failedIds)} keep their ids, and their existing output is overwritten. Read the logs first if you still need them.`,
                        actionTitle: "Restart in Place",
                        destructive: true,
                      },
                    },
                  )
                }
              />
            ) : null}
            {s.finished > 0 ? (
              <Action
                title="Clean Finished Tasks in Group"
                icon={Icon.Trash}
                style={Action.Style.Destructive}
                onAction={() =>
                  onAct(
                    { op: "clean", group: name },
                    {
                      verb: `Cleaning ${name}`,
                      done: `Cleaned ${s.finished} task${s.finished === 1 ? "" : "s"} from ${name}`,
                      confirm: {
                        title: `Remove ${s.finished} finished task${s.finished === 1 ? "" : "s"} from “${name}”?`,
                        message:
                          s.failed > 0
                            ? `The ${s.failed} failure${s.failed === 1 ? "" : "s"} go too, with their logs. Nothing running or queued is touched.`
                            : "Their logs go with them. Nothing running or queued is touched.",
                        actionTitle: "Clean",
                        destructive: true,
                      },
                    },
                  )
                }
              />
            ) : null}
            {s.succeeded > 0 && s.failed > 0 ? (
              <Action
                title="Clean Only the Successes in Group"
                icon={Icon.Trash}
                onAction={() =>
                  onAct(
                    { op: "clean", group: name, successfulOnly: true },
                    {
                      verb: `Cleaning ${name}`,
                      done: `Cleaned ${s.succeeded} successful task${s.succeeded === 1 ? "" : "s"}`,
                      confirm: {
                        title: `Remove ${s.succeeded} successful task${s.succeeded === 1 ? "" : "s"} from “${name}”?`,
                        message: `The ${s.failed} failure${s.failed === 1 ? " stays" : "s stay"} put, so you can still read the logs.`,
                        actionTitle: "Clean Successes",
                        rememberChoice: true,
                      },
                    },
                  )
                }
              />
            ) : null}
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
                      message: `${s.running} running task${s.running === 1 ? "" : "s"} will be killed, and the group will also be paused.`,
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
                          s.total > 0
                            ? `Its ${s.total} task${s.total === 1 ? "" : "s"} will be moved to the default group, not deleted.`
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
              title={props.showDetail ? "Hide Detail" : "Show Detail"}
              icon={Icon.Sidebar}
              shortcut={{ modifiers: ["cmd", "shift"], key: "d" }}
              onAction={props.onToggleDetail}
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

/**
 * The pqsum table, as a Raycast pane.
 *
 * Everything here is derived; nothing is fetched. The breakdown is a TagList
 * rather than one row per state so an idle group stays two lines instead of
 * seven, and only the states that have tasks appear at all.
 */
function GroupDetail({ summary: s }: { summary: GroupSummary }) {
  const avg = formatDuration(s.avgMs);
  const eta = formatDuration(s.etaMs);
  const elapsed = formatDuration(s.elapsedMs);

  const breakdown: { text: string; color: Color }[] = [
    ...(s.running > 0
      ? [{ text: `${s.running} running`, color: Color.Blue }]
      : []),
    ...(s.queued > 0
      ? [{ text: `${s.queued} queued`, color: Color.SecondaryText }]
      : []),
    ...(s.paused > 0
      ? [{ text: `${s.paused} paused`, color: Color.Orange }]
      : []),
    ...(s.stashed > 0
      ? [{ text: `${s.stashed} stashed`, color: Color.SecondaryText }]
      : []),
    ...(s.succeeded > 0
      ? [{ text: `${s.succeeded} succeeded`, color: Color.Green }]
      : []),
    ...(s.failed > 0 ? [{ text: `${s.failed} failed`, color: Color.Red }] : []),
  ];

  return (
    <List.Item.Detail
      metadata={
        <List.Item.Detail.Metadata>
          <List.Item.Detail.Metadata.Label
            title="Progress"
            text={
              s.total === 0
                ? "no tasks"
                : `${s.finished}/${s.total} · ${progressPercent(s.progress)}`
            }
          />
          {breakdown.length > 0 ? (
            <List.Item.Detail.Metadata.TagList title="Breakdown">
              {breakdown.map((b) => (
                <List.Item.Detail.Metadata.TagList.Item
                  key={b.text}
                  text={b.text}
                  color={b.color}
                />
              ))}
            </List.Item.Detail.Metadata.TagList>
          ) : null}
          <List.Item.Detail.Metadata.Separator />
          <List.Item.Detail.Metadata.Label
            title="Daemon"
            text={`${s.status} · ${parallelLabel(s.parallel)} at a time`}
          />
          <List.Item.Detail.Metadata.Separator />
          <List.Item.Detail.Metadata.Label
            title="Average duration"
            text={avg ?? "—"}
          />
          {/* An em dash rather than a number means one of two honest things:
              nothing has finished yet, or only one thing has and a single
              sample is not an estimate. See group-summary.ts. */}
          <List.Item.Detail.Metadata.Label
            title="Estimated remaining"
            text={eta ? `~${eta}` : "—"}
          />
          <List.Item.Detail.Metadata.Label
            title="Elapsed"
            text={elapsed ?? "—"}
          />
          {s.failedIds.length > 0 ? (
            <>
              <List.Item.Detail.Metadata.Separator />
              <List.Item.Detail.Metadata.Label
                title="Failed"
                text={s.failedIds.map((id) => `#${id}`).join(" ")}
              />
            </>
          ) : null}
        </List.Item.Detail.Metadata>
      }
    />
  );
}

/**
 * A number field for a parallelism the preset list doesn't cover.
 *
 * pueue takes any non-negative integer, and a 64-core workstation has every
 * right to ask for 48. The presets stay because they answer the common case in
 * one keystroke; this is the escape hatch, not the default path.
 */
function ParallelismForm(props: {
  name: string;
  current: number;
  onAct: (mutation: Mutation, options: ActOptions) => Promise<boolean>;
}) {
  const { pop } = useNavigation();
  const [value, setValue] = useState(String(props.current));
  const [error, setError] = useState<string | undefined>();

  return (
    <Form
      navigationTitle={`Parallelism for ${props.name}`}
      actions={
        <ActionPanel>
          <Action.SubmitForm
            title="Set Parallelism"
            icon={Icon.BulletPoints}
            onSubmit={async () => {
              const trimmed = value.trim();
              if (!/^\d+$/.test(trimmed)) {
                setError("Whole numbers only, 0 for unlimited");
                return;
              }
              const count = Number(trimmed);
              const ok = await props.onAct(
                { op: "parallel", count, group: props.name },
                {
                  verb: `Setting ${props.name} parallelism`,
                  done: `${props.name} runs ${count === 0 ? "unlimited" : count} at a time`,
                },
              );
              if (ok) pop();
            }}
          />
        </ActionPanel>
      }
    >
      <Form.TextField
        id="parallel"
        title="Parallel Tasks"
        placeholder="16"
        autoFocus
        value={value}
        error={error}
        onChange={(v) => {
          setValue(v);
          if (error) setError(undefined);
        }}
        info="How many tasks this group runs at once. 0 means unlimited — pueue never holds one back, which is what you want for a group of quick, non-competing jobs."
      />
    </Form>
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
