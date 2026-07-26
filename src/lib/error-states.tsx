/**
 * The two structural failures, described once and rendered three ways.
 *
 * The reference extension this is modelled on grew two copies of an
 * `errorMarkdown()` helper that drifted apart; one descriptor with three
 * renderers avoids that. The menu bar needs the same information but cannot
 * render a `List.EmptyView`, so it consumes `describeError()` directly.
 */

import {
  Action,
  ActionPanel,
  Color,
  Detail,
  Icon,
  List,
  openExtensionPreferences,
  showToast,
  Toast,
  type Image,
  Keyboard,
} from "@raycast/api";
import { execFile } from "node:child_process";

import {
  cleanStderr,
  firstLine,
  isBadQuery,
  isBinaryMissing,
  isHostUnreachable,
  isDaemonDown,
  isBrewManagedDaemon,
  PueueError,
  resolveBrew,
} from "./pueue";

export const INSTALL_BREW = "brew install pueue";
export const INSTALL_CARGO = "cargo install --locked pueue";
export const START_BREW = "brew services start pueue";
export const START_MANUAL = "pueued -d";
export const PUEUE_DOCS = "https://github.com/Nukesor/pueue/wiki";

/**
 * Why the daemon should not be started from Raycast unless launchd owns it.
 *
 * This is the same launchd problem as our own binary resolution, except it
 * fails quietly and permanently instead of loudly and once.
 */
const DAEMON_PARENT_WARNING =
  "Starting `pueued -d` from Raycast would make the daemon a child of Raycast's launchd process, so **every task it later runs would inherit Raycast's minimal environment** — no `~/.zshrc`, a bare `PATH`. Start it from a terminal or with `brew services` so your tasks get a usable environment.";

export interface ErrorAction {
  id: string;
  title: string;
  icon: Image.ImageLike;
  /** Copy-to-clipboard actions carry their payload so the menu bar can reuse them. */
  copy?: string;
  url?: string;
  run?: () => void | Promise<void>;
}

export interface ErrorDescriptor {
  icon: Image.ImageLike;
  title: string;
  /** A few words, for the stale banner — the list column is a third of the
   * window when the detail pane is open and truncates anything longer. */
  shortTitle: string;
  description: string;
  markdown: string;
  actions: ErrorAction[];
  /** True for the two states that deserve a whole screen rather than a toast. */
  structural: boolean;
}

/** Start the daemon through launchd, so tasks inherit a login environment. */
async function startDaemonViaBrew(): Promise<void> {
  const brew = resolveBrew();
  if (!brew) return;
  const toast = await showToast({
    style: Toast.Style.Animated,
    title: "Starting pueued…",
  });
  await new Promise<void>((resolve) => {
    execFile(brew, ["services", "start", "pueue"], (err, _stdout, stderr) => {
      if (err) {
        toast.style = Toast.Style.Failure;
        toast.title = "Could not start pueued";
        toast.message = firstLine(cleanStderr(String(stderr ?? err.message)));
      } else {
        toast.style = Toast.Style.Success;
        toast.title = "pueued started";
      }
      resolve();
    });
  });
}

function fence(text: string): string {
  return text.trim()
    ? ["```text", text.replace(/```/g, "``​`"), "```"].join("\n")
    : "";
}

export function describeError(
  error: unknown,
  connection?: { remote: boolean; name: string },
): ErrorDescriptor {
  const detail =
    error instanceof PueueError
      ? error.detail
      : String((error as Error)?.message ?? error);

  if (isBinaryMissing(error)) {
    return {
      icon: Icon.Download,
      title: "Pueue CLI not found",
      shortTitle: "Pueue not found",
      description:
        "Install pueue, then reopen. If it lives somewhere unusual, set its path in preferences.",
      markdown: [
        "# Pueue CLI not found",
        "",
        "This extension drives the `pueue` command line tool — it does not queue anything itself.",
        "",
        "```sh",
        INSTALL_BREW,
        "# or",
        INSTALL_CARGO,
        "```",
        "",
        "### Already installed?",
        "",
        "Raycast runs extensions under launchd, which never sources your shell rc — so Homebrew and `~/.cargo/bin` are **not** on its `PATH`, even though they are in your terminal. The extension probes these directories:",
        "",
        "- `/opt/homebrew/bin` (Apple Silicon Homebrew)",
        "- `/usr/local/bin` (Intel Homebrew)",
        "- `~/.cargo/bin`",
        "- `~/.local/bin`",
        "",
        "If yours is elsewhere, set **Pueue Binary Path** in this extension's preferences to the absolute path.",
      ].join("\n"),
      actions: [
        {
          id: "brew",
          title: "Copy Homebrew Install Command",
          icon: Icon.Clipboard,
          copy: INSTALL_BREW,
        },
        {
          id: "cargo",
          title: "Copy Cargo Install Command",
          icon: Icon.Clipboard,
          copy: INSTALL_CARGO,
        },
        {
          id: "prefs",
          title: "Open Extension Preferences",
          icon: Icon.Gear,
          run: openExtensionPreferences,
        },
        {
          id: "docs",
          title: "Open Pueue Documentation",
          icon: Icon.Book,
          url: PUEUE_DOCS,
        },
      ],
      structural: true,
    };
  }

  if (isDaemonDown(error)) {
    // Never offer to start a daemon we aren't running. `brew services start`
    // would start the *local* one, which is not the daemon that just failed.
    const remote = connection?.remote === true;
    const brewManaged = !remote && isBrewManagedDaemon();
    return {
      icon: Icon.Plug,
      title: "Pueue daemon not running",
      shortTitle: "Daemon not running",
      description: remote
        ? `Couldn't reach ${connection?.name ?? "the remote daemon"}. Check the SSH tunnel, then reload.`
        : "pueue is installed but pueued isn't reachable. Start the daemon, then reload.",
      markdown: [
        "# Pueue daemon not running",
        "",
        "`pueue` is installed, but `pueued` did not answer.",
        "",
        "```sh",
        START_BREW,
        "# or, from a terminal",
        START_MANUAL,
        "```",
        "",
        `> ${DAEMON_PARENT_WARNING}`,
        "",
        "### What pueue reported",
        "",
        fence(detail),
        "",
        "`Did you start the daemon at least once?` means pueued has **never** run — there is no shared secret file yet. `while connecting to daemon` means it ran and has since stopped.",
      ].join("\n"),
      actions: [
        ...(brewManaged
          ? [
              {
                id: "start",
                title: "Start Daemon (brew services)",
                icon: Icon.Play,
                run: startDaemonViaBrew,
              },
            ]
          : []),
        {
          id: "copy-brew",
          title: `Copy “${START_BREW}”`,
          icon: Icon.Clipboard,
          copy: START_BREW,
        },
        {
          id: "copy-manual",
          title: `Copy “${START_MANUAL}”`,
          icon: Icon.Clipboard,
          copy: START_MANUAL,
        },
        {
          id: "prefs",
          title: "Open Extension Preferences",
          icon: Icon.Gear,
          run: openExtensionPreferences,
        },
      ],
      structural: true,
    };
  }

  if (isHostUnreachable(error)) {
    const host = connection?.name ?? "the remote host";
    return {
      icon: Icon.Globe,
      title: `Can't reach ${host}`,
      shortTitle: "Host unreachable",
      description:
        "SSH could not connect. This is a network or SSH problem, not a pueue one.",
      markdown: [
        `# Can't reach ${host}`,
        "",
        "SSH could not open a connection, so nothing pueue-related has been",
        "attempted yet. The daemon may be perfectly healthy.",
        "",
        "### What pueue's SSH attempt reported",
        "",
        fence(detail),
        "",
        "### Things to check",
        "",
        "- Is the host reachable at all? `ssh " +
          (connection?.name ?? "your-host") +
          " true`",
        "- Is the name right? The connection's host field is passed to `ssh`",
        "  verbatim, so a `~/.ssh/config` alias works — but a typo looks exactly",
        "  like an unreachable machine.",
        "- Is key or agent auth set up? The extension passes `BatchMode=yes`, so a",
        "  password prompt fails immediately rather than hanging with nowhere to type.",
        "- Is the machine asleep, or on a network you're no longer on?",
        "",
        "The connection attempt is capped at five seconds, so this is a fast",
        "failure rather than a hang.",
      ].join("\n"),
      actions: [
        {
          id: "prefs",
          title: "Open Extension Preferences",
          icon: Icon.Gear,
          run: openExtensionPreferences,
        },
        {
          id: "copy-test",
          title: "Copy an SSH Test Command",
          icon: Icon.Clipboard,
          copy: `ssh -o BatchMode=yes -o ConnectTimeout=5 ${connection?.name ?? "your-host"} 'pueue status'`,
        },
      ],
      structural: true,
    };
  }

  if (isBadQuery(error)) {
    return {
      icon: Icon.MagnifyingGlass,
      title: "That query didn't parse",
      shortTitle: "Bad query",
      description: "Clear the query argument, or fix the expression.",
      markdown: [
        "# That query didn't parse",
        "",
        fence(detail),
        "",
        "### Grammar",
        "",
        "```text",
        "[columns=id,status,…] [filter]* [order_by <column> asc|desc] [first|last N]",
        "",
        "filter columns   status | command | label | start | end | enqueue_at",
        "operators        =  !=  <  >  %=      (%= means “contains”)",
        "status values    queued | stashed | paused | running | success | failed",
        "```",
        "",
        "Examples: `status=failed`, `command%=cargo order_by id desc`, `last 20`.",
      ].join("\n"),
      actions: [
        {
          id: "docs",
          title: "Open Pueue Documentation",
          icon: Icon.Book,
          url: PUEUE_DOCS,
        },
      ],
      structural: false,
    };
  }

  return {
    icon: Icon.Warning,
    title: firstLine(detail) || "Pueue command failed",
    shortTitle: "Pueue error",
    description: "Something went wrong talking to pueue.",
    markdown: ["# Pueue command failed", "", fence(detail)].join("\n"),
    actions: [
      { id: "copy", title: "Copy Error", icon: Icon.Clipboard, copy: detail },
      {
        id: "prefs",
        title: "Open Extension Preferences",
        icon: Icon.Gear,
        run: openExtensionPreferences,
      },
    ],
    structural: false,
  };
}

function renderActions(d: ErrorDescriptor, onRetry?: () => void) {
  return (
    <ActionPanel>
      {d.actions.map((a) =>
        a.copy !== undefined ? (
          <Action.CopyToClipboard
            key={a.id}
            title={a.title}
            icon={a.icon}
            content={a.copy}
          />
        ) : a.url !== undefined ? (
          <Action.OpenInBrowser
            key={a.id}
            title={a.title}
            icon={a.icon}
            url={a.url}
          />
        ) : (
          <Action
            key={a.id}
            title={a.title}
            icon={a.icon}
            onAction={() => a.run?.()}
          />
        ),
      )}
      {onRetry ? (
        <Action
          title="Reload"
          icon={Icon.ArrowClockwise}
          shortcut={Keyboard.Shortcut.Common.Refresh}
          onAction={onRetry}
        />
      ) : null}
    </ActionPanel>
  );
}

/**
 * Must be rendered *inside* a `<List>` — `List.EmptyView` is not standalone.
 */
export function ErrorEmptyView({
  error,
  onRetry,
  connection,
}: {
  error: unknown;
  onRetry?: () => void;
  connection?: { remote: boolean; name: string };
}) {
  const d = describeError(error, connection);
  return (
    <List.EmptyView
      icon={d.icon}
      title={d.title}
      description={d.description}
      actions={renderActions(d, onRetry)}
    />
  );
}

/**
 * A row that says the list underneath it can't be trusted.
 *
 * `useCachedPromise` keeps serving its last successful read when a fetch fails,
 * which is the right default — but with the daemon stopped it means a dead
 * queue renders as a live one, and Raycast's failure toast is a single line at
 * the bottom that scrolls away. A task list that is quietly six hours old is
 * worse than no task list, so structural failures get a row of their own,
 * carrying the same recovery actions as the full-screen view.
 */
export function StaleBannerItem({
  error,
  onRetry,
  connection,
}: {
  error: unknown;
  onRetry?: () => void;
  connection?: { remote: boolean; name: string };
}) {
  const d = describeError(error, connection);
  return (
    <List.Item
      icon={{ source: Icon.Warning, tintColor: Color.Red }}
      title={d.shortTitle}
      accessories={[{ text: "cached" }]}
      actions={renderActions(d, onRetry)}
    />
  );
}

export function ErrorDetail({
  error,
  onRetry,
  connection,
}: {
  error: unknown;
  onRetry?: () => void;
  connection?: { remote: boolean; name: string };
}) {
  const d = describeError(error, connection);
  return <Detail markdown={d.markdown} actions={renderActions(d, onRetry)} />;
}
