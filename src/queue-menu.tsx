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
  openExtensionPreferences,
  showHUD,
  updateCommandMetadata,
  type Image,
} from "@raycast/api";
import { useCachedPromise, useCachedState } from "@raycast/utils";

import { connectionIcon } from "./lib/connection-ui";
import { describeError } from "./lib/error-states";
import { formatDuration, groupProgressIcon, statusTag } from "./lib/format";
import {
  parallelLabel,
  progressBar,
  progressPercent,
  summarizeGroups,
  type GroupSummary,
} from "./lib/group-summary";
import {
  connectionByName,
  connections,
  forConnection,
  hasEverRun,
  isFailed,
  isPaused,
  isQueued,
  isRunning,
  mutate,
  oneline,
  snapshot,
  taskList,
  LOCAL_CONNECTION_NAME,
  type Connection,
  type Mutation,
  type Task,
} from "./lib/pueue";

const MENU_ICON = "pueue-menubar.svg";

/**
 * Parallelism presets offered in the menu, kept shorter than the Groups view's.
 * A menu is a list you scan, not a form you fill in; anything past this goes
 * through "Other…", which opens the view where a number can be typed.
 */
const MENU_PARALLEL_CHOICES = [1, 2, 4, 6, 8, 12, 0];

export default function Command() {
  const prefs = getPreferenceValues<Preferences.QueueMenu>();
  const perSection = Math.max(1, Number(prefs.maxItemsPerSection) || 7);

  /**
   * Whether destructive menu bar items hide behind ⌥.
   *
   * The ⌥ hold *is* this menu's confirmation — `confirmAlert` presents in the
   * Raycast window, which is closed while the menu is open, so there is no
   * dialog to show. It therefore answers to the same preference: someone who
   * turned confirmations off has said they don't want friction on destructive
   * actions, and honouring that everywhere except here would be a surprise.
   */
  const guardDestructive =
    getPreferenceValues<Preferences>().confirmDestructive;

  // Shares the selection with the view commands, so the menu bar and Tasks
  // never disagree about which daemon you're looking at.
  const [connectionName, setConnectionName] = useCachedState(
    "connection.name",
    LOCAL_CONNECTION_NAME,
  );
  const connection = connectionByName(connectionName);
  const allConnections = connections();

  const {
    data: cached,
    error,
    isLoading,
    revalidate,
  } = useCachedPromise(
    (query: string, name: string) =>
      snapshot({ query, connection: connectionByName(name) }),
    [prefs.menuQuery ?? "", connectionName],
    {
      keepPreviousData: true,
      onData: (snap) => {
        const tasks = taskList(snap.state.tasks);
        const running = tasks.filter(isRunning).length;
        const queued = tasks.filter(isQueued).length;
        // Only affects this command's own subtitle in root search.
        // Name the daemon in the subtitle when it isn't this machine —
        // otherwise the counts are ambiguous the moment a remote is selected.
        const where = connection.remote ? `${connection.name}: ` : "";
        updateCommandMetadata({
          subtitle: `${where}${running} running · ${queued} queued`,
        }).catch(() => {});
      },
    },
  );

  // The hook's `keepPreviousData` will serve the last successful read from
  // *another* daemon when this one has no cache entry yet, which is how an
  // unreachable host used to render a plausible-looking `default` group that
  // belonged to a different machine. Reject anything that isn't ours.
  const data = forConnection(cached, connection.name);

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
      context: { logTaskId: taskId, connectionName: connection.name },
    }).catch(() => {});
  }

  /** Run a mutation from a menu item. No window, so feedback is a HUD. */
  async function run(m: Mutation, done: string) {
    try {
      await mutate(m, { connection });
      await showHUD(done);
      revalidate();
    } catch (error) {
      const d = describeError(error);
      await showHUD(d.shortTitle);
    }
  }

  // No data *for this connection* and a failure: render the error rather than
  // vanishing. Returning null would remove the item entirely, which is the
  // wrong answer for someone who deliberately enabled a pueue menu bar command.
  //
  // The `!error` arm matters too: without it a connection switch renders
  // ErrorMenu with no error at all, whose title becomes the literal string
  // "undefined".
  if (!data) {
    return isLoading || !error ? (
      <MenuBarExtra
        icon={{ source: MENU_ICON, tintColor: Color.SecondaryText }}
        isLoading
      />
    ) : (
      <ErrorMenu
        error={error}
        onRetry={revalidate}
        connection={connection}
        all={allConnections}
        onSelect={setConnectionName}
      />
    );
  }

  const tasks = taskList(data.state.tasks);
  const running = tasks.filter(isRunning);
  const queued = tasks.filter(isQueued);
  const paused = tasks.filter(isPaused);
  const failed = tasks.filter(isFailed);
  const groups = summarizeGroups(data.state.groups, tasks);
  const allPaused =
    groups.length > 0 && groups.every((g) => g.status === "Paused");

  // Data for this connection, but the latest read failed. What is on screen is
  // not a moment old — it is a snapshot of a queue we can no longer see, and
  // Raycast's failure toast is a line at the bottom of a window that isn't
  // open. The view commands say so with StaleBannerItem; this is that.
  const staleError =
    error !== undefined && describeError(error, connection).structural
      ? error
      : undefined;

  return (
    <MenuBarExtra
      icon={menuIcon({
        failed: failed.length,
        allPaused,
        stale: staleError !== undefined,
      })}
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
        connection,
        staleError,
      )}
      isLoading={isLoading}
    >
      <StaleSection
        error={staleError}
        onRetry={revalidate}
        connection={connection}
      />

      <TaskSection
        title="Running"
        tasks={running}
        limit={perSection}
        onShowLog={showLog}
        guardDestructive={guardDestructive}
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
        guardDestructive={guardDestructive}
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
        guardDestructive={guardDestructive}
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
          {groups.map((s) => (
            <GroupSubmenu
              key={s.name}
              summary={s}
              run={run}
              guardDestructive={guardDestructive}
            />
          ))}
        </MenuBarExtra.Section>
      ) : null}

      <ConnectionSection
        all={allConnections}
        current={connection}
        onSelect={setConnectionName}
      />

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
        {guardDestructive ? (
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
        ) : (
          <MenuBarExtra.Item
            title="Clean Finished Tasks"
            icon={Icon.Trash}
            onAction={() => run({ op: "clean" }, "Cleaned finished tasks")}
          />
        )}
      </MenuBarExtra.Section>

      <MenuBarExtra.Section>
        {/* No onAction: a disabled row, used as a staleness label. Raycast
            restores a menu bar item from its database rather than by re-running
            the command, so a stale render can outlive a restart — this is what
            makes that visible instead of misleading. */}
        <MenuBarExtra.Item
          title={
            staleError !== undefined
              ? `Frozen at ${clock(data.fetchedAt)}`
              : `Updated ${clock(data.fetchedAt)}`
          }
        />
        <MenuBarExtra.Item
          title="Refresh"
          icon={Icon.ArrowClockwise}
          onAction={() => revalidate()}
        />
        {/* Reachable while things work, not only after a failure — adding or
            correcting a connection is a thing you do deliberately, not just
            in response to an error. */}
        <MenuBarExtra.Item
          title="Extension Preferences…"
          icon={Icon.Gear}
          onAction={openExtensionPreferences}
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
  guardDestructive: boolean;
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
                a.alternate && props.guardDestructive ? (
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

/**
 * The connection switcher, shared by the normal menu and the error menu.
 *
 * It has to exist in BOTH. Selecting a remote that turns out to be
 * unreachable renders the error menu, and if the switcher only lived in the
 * normal menu there would be no way back to Local — the menu bar would be
 * permanently stuck on a daemon it cannot reach. Escape routes have to live
 * in the broken state, not only in the working one.
 */
function ConnectionSection({
  all,
  current,
  onSelect,
}: {
  all: Connection[];
  current: Connection;
  onSelect: (name: string) => void;
}) {
  if (all.length < 2) return null;
  return (
    <MenuBarExtra.Section title="Connection">
      {all.map((c) => (
        <MenuBarExtra.Item
          key={c.name}
          title={c.sshHost ? `${c.name} (${c.sshHost})` : c.name}
          icon={c.name === current.name ? Icon.Checkmark : connectionIcon(c)}
          onAction={() => onSelect(c.name)}
        />
      ))}
    </MenuBarExtra.Section>
  );
}

/**
 * One group, with its progress in the title and its batch actions inside.
 *
 * The title used to be `name — running (0/1)`: running tasks over parallelism,
 * neither of which moves while a batch works through. It now leads with
 * `8/20`, which is the number you opened the menu for.
 *
 * Reset is not offered here as a plain item and never will be. It kills every
 * task in the group, deletes them, and deletes their logs — and there is no
 * dialog available in a menu bar to confirm that against. It sits behind an ⌥
 * that, uniquely, does *not* answer to the confirmations preference, and
 * "Open Groups…" is one click away for anyone who wants the real thing with a
 * real alert.
 */
function GroupSubmenu(props: {
  summary: GroupSummary;
  run: (m: Mutation, done: string) => void;
  guardDestructive: boolean;
}) {
  const { summary: s, run } = props;
  const eta = formatDuration(s.etaMs);
  const running = s.status === "Running";

  return (
    <MenuBarExtra.Submenu title={groupTitle(s)} icon={groupProgressIcon(s)}>
      {/* No onAction: the disabled first row, the same slot a task submenu uses
          for its status. This is what makes the menu glanceable — the numbers
          are visible without opening anything further. */}
      <MenuBarExtra.Item
        title={
          s.total === 0
            ? "No tasks"
            : `${progressBar(s.progress)} ${progressPercent(s.progress)}`
        }
        subtitle={
          s.total === 0
            ? undefined
            : [
                `${s.running}/${parallelLabel(s.parallel)} slots`,
                ...(eta ? [`~${eta} left`] : []),
              ].join(" · ")
        }
      />

      <MenuBarExtra.Section>
        <MenuBarExtra.Item
          title={running ? "Pause Group" : "Resume Group"}
          icon={running ? Icon.Pause : Icon.Play}
          onAction={() =>
            running
              ? run({ op: "pause", group: s.name }, `Paused ${s.name}`)
              : run({ op: "start", group: s.name }, `Resumed ${s.name}`)
          }
        />
        <MenuBarExtra.Submenu title="Parallelism" icon={Icon.BulletPoints}>
          {MENU_PARALLEL_CHOICES.map((n) => (
            <MenuBarExtra.Item
              key={n}
              title={n === 0 ? "Unlimited" : String(n)}
              icon={n === s.parallel ? Icon.Checkmark : Icon.Circle}
              onAction={() =>
                run(
                  { op: "parallel", count: n, group: s.name },
                  `${s.name}: ${n === 0 ? "unlimited" : n} at a time`,
                )
              }
            />
          ))}
          {/* Free text is not enterable in a menu, so anything off this list
              has to be set where a form can exist. */}
          <MenuBarExtra.Item
            title="Other…"
            icon={Icon.Pencil}
            onAction={() => openGroups()}
          />
        </MenuBarExtra.Submenu>
      </MenuBarExtra.Section>

      {s.failed > 0 || s.finished > 0 ? (
        <MenuBarExtra.Section>
          {/* pueue accepts --failed-in-group on a group with no failures and
              exits 0 without a word, so this is hidden rather than disabled. */}
          {s.failed > 0 ? (
            <MenuBarExtra.Item
              title={`Restart ${s.failed} Failed (New Tasks)`}
              icon={Icon.Redo}
              onAction={() =>
                run(
                  { op: "restart", failedInGroup: s.name },
                  `Restarted ${s.failed} in ${s.name}`,
                )
              }
            />
          ) : null}
          {s.failed > 0 ? (
            <MenuBarExtra.Item
              title={`Restart ${s.failed} Failed in Place (Overwrites Logs)`}
              icon={Icon.Repeat}
              onAction={() =>
                run(
                  { op: "restart", failedInGroup: s.name, inPlace: true },
                  `Restarted ${s.failed} in place`,
                )
              }
            />
          ) : null}
          {s.finished > 0 ? (
            <GuardedItem
              title="Clean Finished Tasks in Group"
              icon={Icon.Trash}
              guarded={props.guardDestructive}
              onAction={() =>
                run(
                  { op: "clean", group: s.name },
                  `Cleaned ${s.finished} from ${s.name}`,
                )
              }
            />
          ) : null}
        </MenuBarExtra.Section>
      ) : null}

      <MenuBarExtra.Section>
        {/* Always behind ⌥, regardless of the confirmations preference. This is
            not the exception that was removed from Remove and Clean: the Groups
            view already treats reset as categorically worse than the rest and
            asks every single time, with no "don't show this again". Reset kills
            every task in the group, deletes them, and deletes their logs. */}
        <GuardedItem
          title="Reset Group (Deletes Every Task and Log)"
          icon={Icon.ExclamationMark}
          guarded
          onAction={() =>
            run({ op: "reset", groups: [s.name] }, `Reset ${s.name}`)
          }
        />
      </MenuBarExtra.Section>

      <MenuBarExtra.Section>
        <MenuBarExtra.Item
          title="Show Tasks in Group"
          icon={Icon.AppWindowList}
          onAction={() =>
            launchCommand({
              name: "tasks",
              type: LaunchType.UserInitiated,
              context: { group: s.name },
            }).catch(() => {})
          }
        />
        <MenuBarExtra.Item
          title="Open Groups…"
          icon={Icon.Sidebar}
          onAction={openGroups}
        />
      </MenuBarExtra.Section>
    </MenuBarExtra.Submenu>
  );
}

function openGroups() {
  launchCommand({ name: "groups", type: LaunchType.UserInitiated }).catch(
    () => {},
  );
}

/** `wf · 6/6 · 5 failed`. `idle` when there is nothing to be a fraction of. */
function groupTitle(s: GroupSummary): string {
  if (s.total === 0) {
    return `${s.name} · idle${s.status === "Paused" ? " · paused" : ""}`;
  }
  const parts = [s.name, `${s.finished}/${s.total}`];
  if (s.failed > 0) parts.push(`${s.failed} failed`);
  if (s.status === "Paused") parts.push("paused");
  return parts.join(" · ");
}

/**
 * A one-click item, or an ⌥-held one.
 *
 * ⌥ is this menu's confirmation — `confirmAlert` presents in the Raycast
 * window, which is closed while the menu is open, so there is no dialog to
 * show. That is why it normally answers to the same preference a dialog does.
 */
function GuardedItem(props: {
  title: string;
  icon: Icon;
  guarded: boolean;
  onAction: () => void;
}) {
  if (!props.guarded) {
    return (
      <MenuBarExtra.Item
        title={props.title}
        icon={props.icon}
        onAction={props.onAction}
      />
    );
  }
  return (
    <MenuBarExtra.Item
      title={`Hold ⌥ to ${props.title}`}
      icon={props.icon}
      alternate={
        <MenuBarExtra.Item
          title={props.title}
          icon={props.icon}
          onAction={props.onAction}
        />
      }
    />
  );
}

/**
 * A header saying the queue below it can no longer be reached.
 *
 * The counterpart to `StaleBannerItem` in the view commands. Without it a dead
 * daemon renders as a live queue for as long as the cache survives, which is
 * worse than rendering nothing — the whole value of a menu bar item is that you
 * trust it at a glance.
 */
function StaleSection({
  error,
  onRetry,
  connection,
}: {
  error: unknown;
  onRetry: () => void;
  connection: Connection;
}) {
  if (error === undefined) return null;
  const d = describeError(error, connection);
  return (
    <MenuBarExtra.Section>
      <MenuBarExtra.Item
        title={d.shortTitle}
        subtitle="showing cached data"
        icon={{ source: Icon.Warning, tintColor: Color.Red }}
        tooltip={d.description}
      />
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
  );
}

function ErrorMenu({
  error,
  onRetry,
  connection,
  all,
  onSelect,
}: {
  error: unknown;
  onRetry: () => void;
  connection: Connection;
  all: Connection[];
  onSelect: (name: string) => void;
}) {
  const d = describeError(error, connection);
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

      {/* The way out. Without it a broken remote is a one-way door. */}
      <ConnectionSection all={all} current={connection} onSelect={onSelect} />
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
 *
 * Stale wins over everything else: when the numbers can't be trusted, saying
 * "one task failed" in red is a claim we are in no position to make.
 */
function menuIcon(s: {
  failed: number;
  allPaused: boolean;
  stale: boolean;
}): Image.ImageLike {
  if (s.stale) return { source: MENU_ICON, tintColor: Color.SecondaryText };
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
  connection: Connection,
  staleError: unknown,
): string {
  const where = connection.remote ? connection.name : "Pueue";
  // Don't recite counts we can't stand behind — lead with why they're frozen.
  if (staleError !== undefined) {
    return `${where} — ${describeError(staleError, connection).shortTitle} · showing ${clock(fetchedAt)}`;
  }
  const parts = [`${running} running`, `${queued} queued`];
  if (failed > 0) parts.push(`${failed} failed`);
  return `${where} — ${parts.join(", ")} · updated ${clock(fetchedAt)}`;
}

function clock(ms: number): string {
  return new Date(ms).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}
