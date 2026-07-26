/**
 * The queue, in the menu bar.
 *
 * This is the closest thing Raycast has to a widget: there is no widget API,
 * no desktop surface, and no Notification Center extension point. A
 * `mode: "menu-bar"` command with a manifest `interval` is the only way to see
 * something without opening Raycast.
 *
 * Four constraints shape the whole file:
 *
 *   1. There is no badge API. The count *is* the title, and `undefined` is what
 *      removes it — that's the idiom Homebrew's services-menu uses too.
 *   2. `isLoading` is a contract, not a hint: leave it unset and Raycast
 *      renders then immediately unloads; leave it true and the whole React
 *      tree re-runs every tick.
 *   3. On restart Raycast restores the item from its database rather than by
 *      re-running the command, so a stale render can outlive a restart. The
 *      "Updated HH:MM" row exists to make that visible instead of misleading.
 *   4. No `confirmAlert`. It presents in the Raycast window, which is closed
 *      when the menu is open — a silently swallowed confirmation on a
 *      destructive action is not acceptable, so destructive items live behind
 *      ⌥ instead.
 */

import {
  Clipboard,
  Color,
  Icon,
  LaunchType,
  MenuBarExtra,
  getPreferenceValues,
  launchCommand,
  open,
  showHUD,
  updateCommandMetadata,
  type Image,
} from "@raycast/api";
import { useCachedPromise } from "@raycast/utils";

import { describeError } from "./lib/error-states";
import { statusTag } from "./lib/format";
import {
  defaultConnection,
  hasEverRun,
  isFailed,
  isPaused,
  isQueued,
  isRunning,
  mutate,
  oneline,
  snapshot,
  taskList,
  type Mutation,
  type Task,
} from "./lib/pueue";

const MENU_ICON = "pueue-menubar.svg";

export default function Command() {
  const prefs = getPreferenceValues<Preferences.QueueMenu>();
  const perSection = Math.max(1, Number(prefs.maxItemsPerSection) || 7);

  const { data, error, isLoading, revalidate } = useCachedPromise(
    (query: string) => snapshot({ query }),
    [prefs.menuQuery ?? ""],
    {
      keepPreviousData: true,
      onData: (snap) => {
        const tasks = taskList(snap.state.tasks);
        const running = tasks.filter(isRunning).length;
        const queued = tasks.filter(isQueued).length;
        // Only affects this command's own subtitle in root search.
        updateCommandMetadata({
          subtitle: `${running} running · ${queued} queued`,
        }).catch(() => {});
      },
    },
  );

  /**
   * Open the Tasks command on this task's log.
   *
   * The menu bar can show that something failed but not *why*, and "Restart" or
   * "Remove" isn't a decision anyone can make without reading the error first.
   * This is the way out of the menu into the detail.
   */
  function showLog(taskId: number) {
    launchCommand({
      name: "tasks",
      type: LaunchType.UserInitiated,
      // The connection has to travel with the id. Tasks remembers whichever
      // daemon you last selected, and the menu bar always reads the default —
      // so without this, a task id from one daemon gets looked up on another
      // and the log silently fails to open.
      context: {
        logTaskId: taskId,
        connectionName: defaultConnection().name,
      },
    }).catch(() => {});
  }

  /** Run a mutation from a menu item. No window, so feedback is a HUD. */
  async function run(m: Mutation, done: string) {
    try {
      await mutate(m);
      await showHUD(done);
      revalidate();
    } catch (error) {
      const d = describeError(error);
      await showHUD(d.shortTitle);
    }
  }

  // No cached data and a failure: render the error rather than vanishing.
  // Returning null would remove the item entirely, which is the wrong answer
  // for someone who deliberately enabled a pueue menu bar command.
  if (!data) {
    return isLoading ? (
      <MenuBarExtra
        icon={{ source: MENU_ICON, tintColor: Color.SecondaryText }}
        isLoading
      />
    ) : (
      <ErrorMenu error={error} onRetry={revalidate} />
    );
  }

  const tasks = taskList(data.state.tasks);
  const running = tasks.filter(isRunning);
  const queued = tasks.filter(isQueued);
  const paused = tasks.filter(isPaused);
  const failed = tasks.filter(isFailed);
  const groups = Object.entries(data.state.groups).sort(([a], [b]) =>
    a.localeCompare(b),
  );
  const allPaused =
    groups.length > 0 && groups.every(([, g]) => g.status === "Paused");

  return (
    <MenuBarExtra
      icon={menuIcon({ failed: failed.length, allPaused })}
      title={menuTitle(
        prefs.titleCounts,
        running.length,
        queued.length,
        paused.length,
      )}
      tooltip={tooltip(
        running.length,
        queued.length,
        failed.length,
        data.fetchedAt,
      )}
      isLoading={isLoading}
    >
      <TaskSection
        title="Running"
        tasks={running}
        limit={perSection}
        onShowLog={showLog}
        actionsFor={(task) => [
          {
            title: "Kill",
            icon: Icon.Stop,
            alternate: true,
            m: { op: "kill", ids: [task.id] } as Mutation,
            done: `Killed task ${task.id}`,
          },
          {
            title: "Pause",
            icon: Icon.Pause,
            m: { op: "pause", ids: [task.id] } as Mutation,
            done: `Paused task ${task.id}`,
          },
        ]}
        run={run}
      />

      <TaskSection
        title="Queued"
        tasks={queued}
        limit={perSection}
        onShowLog={showLog}
        actionsFor={(task) => [
          {
            title: "Start Now",
            icon: Icon.Play,
            m: { op: "start", ids: [task.id] } as Mutation,
            done: `Started task ${task.id}`,
          },
          {
            title: "Stash",
            icon: Icon.Tray,
            m: { op: "stash", ids: [task.id] } as Mutation,
            done: `Stashed task ${task.id}`,
          },
          {
            title: "Remove",
            icon: Icon.Trash,
            alternate: true,
            m: { op: "remove", ids: [task.id] } as Mutation,
            done: `Removed task ${task.id}`,
          },
        ]}
        run={run}
      />

      <TaskSection
        title="Failed"
        tasks={failed}
        limit={perSection}
        onShowLog={showLog}
        actionsFor={(task) => [
          // Two genuinely different operations, verified against the daemon:
          // --not-in-place mints a new task id and keeps the old log; --in-place
          // reuses the id and overwrites the log.
          {
            title: "Restart (New Task)",
            icon: Icon.Redo,
            m: { op: "restart", ids: [task.id] } as Mutation,
            done: `Restarted task ${task.id} as a new task`,
          },
          {
            title: "Restart in Place (Overwrites Log)",
            icon: Icon.Repeat,
            m: { op: "restart", ids: [task.id], inPlace: true } as Mutation,
            done: `Restarted task ${task.id} in place`,
          },
          {
            title: "Remove",
            icon: Icon.Trash,
            alternate: true,
            m: { op: "remove", ids: [task.id] } as Mutation,
            done: `Removed task ${task.id}`,
          },
        ]}
        run={run}
      />

      {prefs.showGroups && groups.length > 0 ? (
        <MenuBarExtra.Section title="Groups">
          {groups.map(([name, group]) => (
            <MenuBarExtra.Submenu
              key={name}
              title={`${name} — ${group.status.toLowerCase()} (${
                tasks.filter((t) => t.group === name && isRunning(t)).length
              }/${group.parallel_tasks === 0 ? "∞" : group.parallel_tasks})`}
              icon={group.status === "Running" ? Icon.Play : Icon.Pause}
            >
              {group.status === "Running" ? (
                <MenuBarExtra.Item
                  title="Pause Group"
                  icon={Icon.Pause}
                  onAction={() =>
                    run({ op: "pause", group: name }, `Paused ${name}`)
                  }
                />
              ) : (
                <MenuBarExtra.Item
                  title="Resume Group"
                  icon={Icon.Play}
                  onAction={() =>
                    run({ op: "start", group: name }, `Resumed ${name}`)
                  }
                />
              )}
              <MenuBarExtra.Submenu
                title="Parallelism"
                icon={Icon.BulletPoints}
              >
                {[1, 2, 3, 4, 6, 8, 0].map((n) => (
                  <MenuBarExtra.Item
                    key={n}
                    title={n === 0 ? "Unlimited" : String(n)}
                    icon={
                      n === group.parallel_tasks ? Icon.Checkmark : Icon.Circle
                    }
                    onAction={() =>
                      run(
                        { op: "parallel", count: n, group: name },
                        `${name}: ${n === 0 ? "unlimited" : n} at a time`,
                      )
                    }
                  />
                ))}
              </MenuBarExtra.Submenu>
            </MenuBarExtra.Submenu>
          ))}
        </MenuBarExtra.Section>
      ) : null}

      <MenuBarExtra.Section>
        <MenuBarExtra.Item
          title="Add Task…"
          icon={Icon.Plus}
          onAction={() =>
            launchCommand({ name: "add-task", type: LaunchType.UserInitiated })
          }
        />
        <MenuBarExtra.Item
          title="Open Tasks"
          icon={Icon.AppWindowList}
          onAction={() =>
            launchCommand({ name: "tasks", type: LaunchType.UserInitiated })
          }
        />
        <MenuBarExtra.Item
          title="Pause All"
          icon={Icon.Pause}
          onAction={() => run({ op: "pause", all: true }, "Paused all groups")}
          alternate={
            <MenuBarExtra.Item
              title="Pause All (Let Running Tasks Finish)"
              icon={Icon.Pause}
              onAction={() =>
                run(
                  { op: "pause", all: true, wait: true },
                  "Pausing after current tasks",
                )
              }
            />
          }
        />
        <MenuBarExtra.Item
          title="Start All"
          icon={Icon.Play}
          onAction={() => run({ op: "start", all: true }, "Resumed all groups")}
        />
      </MenuBarExtra.Section>

      <MenuBarExtra.Section>
        {/* ⌥-only: destructive, and there is no confirmation available here. */}
        <MenuBarExtra.Item
          title="Hold ⌥ to Clean Finished Tasks"
          icon={Icon.Trash}
          alternate={
            <MenuBarExtra.Item
              title="Clean Finished Tasks"
              icon={Icon.Trash}
              onAction={() => run({ op: "clean" }, "Cleaned finished tasks")}
            />
          }
        />
      </MenuBarExtra.Section>

      <MenuBarExtra.Section>
        {/* No onAction: a disabled row, used as a staleness label. */}
        <MenuBarExtra.Item title={`Updated ${clock(data.fetchedAt)}`} />
        <MenuBarExtra.Item
          title="Refresh"
          icon={Icon.ArrowClockwise}
          onAction={() => revalidate()}
        />
      </MenuBarExtra.Section>
    </MenuBarExtra>
  );
}

/* -- pieces ---------------------------------------------------------------- */

interface ItemAction {
  title: string;
  icon: Icon;
  m: Mutation;
  done: string;
  /** Destructive actions hide behind ⌥ so they can't be hit by accident. */
  alternate?: boolean;
}

function TaskSection(props: {
  title: string;
  tasks: Task[];
  limit: number;
  actionsFor: (task: Task) => ItemAction[];
  run: (m: Mutation, done: string) => void;
  onShowLog: (taskId: number) => void;
}) {
  if (props.tasks.length === 0) return null;
  const shown = props.tasks.slice(0, props.limit);
  const hidden = props.tasks.length - shown.length;

  return (
    <MenuBarExtra.Section title={`${props.title} (${props.tasks.length})`}>
      {shown.map((task) => (
        // Every title is prefixed with the id, so no two items at the same
        // level are identical — Raycast warns that identical siblings get their
        // onAction handlers crossed.
        <MenuBarExtra.Submenu
          key={task.id}
          title={`${task.id} · ${oneline(task.command, 48)}`}
          icon={sectionIcon(props.title)}
        >
          <MenuBarExtra.Item title={statusTag(task)} />
          <MenuBarExtra.Section>
            {/* First, because deciding what to do about a failure requires
                seeing it. hasEverRun keeps it off tasks with no log at all. */}
            {hasEverRun(task) ? (
              <MenuBarExtra.Item
                title="Show Log…"
                icon={Icon.Text}
                onAction={() => props.onShowLog(task.id)}
              />
            ) : null}
            {props
              .actionsFor(task)
              .map((a) =>
                a.alternate ? (
                  <MenuBarExtra.Item
                    key={a.title}
                    title={`Hold ⌥ to ${a.title}`}
                    icon={a.icon}
                    alternate={
                      <MenuBarExtra.Item
                        title={a.title}
                        icon={a.icon}
                        onAction={() => props.run(a.m, a.done)}
                      />
                    }
                  />
                ) : (
                  <MenuBarExtra.Item
                    key={a.title}
                    title={a.title}
                    icon={a.icon}
                    onAction={() => props.run(a.m, a.done)}
                  />
                ),
              )}
            <MenuBarExtra.Item
              title="Copy Command"
              icon={Icon.Clipboard}
              onAction={() => Clipboard.copy(task.command)}
            />
          </MenuBarExtra.Section>
        </MenuBarExtra.Submenu>
      ))}
      {hidden > 0 ? (
        // A 400-task queue must not become 400 menu items every minute.
        <MenuBarExtra.Item
          title={`…and ${hidden} more`}
          icon={Icon.Ellipsis}
          onAction={() =>
            launchCommand({ name: "tasks", type: LaunchType.UserInitiated })
          }
        />
      ) : null}
    </MenuBarExtra.Section>
  );
}

function ErrorMenu({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry: () => void;
}) {
  const d = describeError(error);
  return (
    <MenuBarExtra
      icon={{ source: Icon.XMarkCircle, tintColor: Color.Red }}
      tooltip={`Pueue — ${d.title}`}
    >
      <MenuBarExtra.Item title={d.title} />
      <MenuBarExtra.Section>
        <MenuBarExtra.Item
          title="Retry"
          icon={Icon.ArrowClockwise}
          onAction={onRetry}
        />
        {d.actions.map((a) => (
          <MenuBarExtra.Item
            key={a.id}
            title={a.title}
            icon={a.icon}
            onAction={() => {
              if (a.copy !== undefined) Clipboard.copy(a.copy);
              else if (a.url !== undefined) open(a.url);
              else a.run?.();
            }}
          />
        ))}
      </MenuBarExtra.Section>
    </MenuBarExtra>
  );
}

/* -- presentation ---------------------------------------------------------- */

function sectionIcon(title: string): Image.ImageLike {
  if (title === "Running")
    return { source: Icon.CircleProgress, tintColor: Color.Blue };
  if (title === "Failed")
    return { source: Icon.XMarkCircle, tintColor: Color.Red };
  return { source: Icon.Clock, tintColor: Color.SecondaryText };
}

/**
 * One glyph, four tints — the shape never moves in the menu bar, only its
 * colour. The asset is a monochrome template so it reads on light and dark.
 */
function menuIcon(s: { failed: number; allPaused: boolean }): Image.ImageLike {
  if (s.failed > 0) return { source: MENU_ICON, tintColor: Color.Red };
  if (s.allPaused) return { source: MENU_ICON, tintColor: Color.Orange };
  return { source: MENU_ICON, tintColor: Color.PrimaryText };
}

/**
 * The count is the title; `undefined` hides it.
 *
 * Running leads, because it is the only number that changes on its own — a
 * queued backlog is something you already know about.
 */
function menuTitle(
  mode: Preferences.QueueMenu["titleCounts"],
  running: number,
  queued: number,
  paused: number,
): string | undefined {
  switch (mode) {
    case "none":
      return undefined;
    case "running-queued":
      return running > 0 || queued > 0 ? `${running}/${queued}` : undefined;
    case "active": {
      const n = running + queued + paused;
      return n > 0 ? String(n) : undefined;
    }
    default:
      return running > 0 ? String(running) : undefined;
  }
}

function tooltip(
  running: number,
  queued: number,
  failed: number,
  fetchedAt: number,
): string {
  const parts = [`${running} running`, `${queued} queued`];
  if (failed > 0) parts.push(`${failed} failed`);
  return `Pueue — ${parts.join(", ")} · updated ${clock(fetchedAt)}`;
}

function clock(ms: number): string {
  return new Date(ms).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}
