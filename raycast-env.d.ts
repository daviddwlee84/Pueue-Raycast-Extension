/// <reference types="@raycast/api">

/* 🚧 🚧 🚧
 * This file is auto-generated from the extension's manifest.
 * Do not modify manually. Instead, update the `package.json` file.
 * 🚧 🚧 🚧 */

/* eslint-disable @typescript-eslint/ban-types */

type ExtensionPreferences = {
  /** Pueue Binary Path - Absolute path to the `pueue` client. Empty = auto-probe /opt/homebrew/bin, /usr/local/bin, ~/.cargo/bin, ~/.local/bin. `pueued` is looked for beside it. Raycast runs under launchd with no shell rc, so a bare `pueue` is never on PATH. */
  "pueuePath"?: string,
  /** Pueue Config Path - Absolute path to pueue.yml. Empty = let pueue use its default (~/Library/Application Support/pueue/pueue.yml on macOS). Also used to locate the task_logs directory. */
  "configPath"?: string,
  /** Remote Connections - One per line. Simplest form is just an SSH host you can already reach — everything runs over ssh, nothing to set up: "local_ubuntu". Or "name | ssh-host" to label it. Advanced: "name | ~/pueue/client.yml | ssh-host" reads through a forwarded socket and submits over ssh. */
  "connections"?: string,
  /** Confirmations - Ask before destructive actions in the view commands. Menu bar destructive actions are always behind ⌥ regardless of this setting. */
  "confirmDestructive": boolean
}

/** Preferences accessible in all the extension's commands */
declare type Preferences = ExtensionPreferences

declare namespace Preferences {
  /** Preferences accessible in the `tasks` command */
  export type Tasks = ExtensionPreferences & {
  /** Detail Pane - Show task metadata and a log preview beside the list. Toggle at any time with ⌘⇧D. */
  "showDetail": boolean,
  /** Log Preview Lines - How many trailing log lines to show in the detail pane for the selected task. Higher values cost one extra `pueue log` call per selection change. */
  "detailLogLines": string
}
  /** Preferences accessible in the `add-task` command */
  export type AddTask = ExtensionPreferences & {}
  /** Preferences accessible in the `quick-add` command */
  export type QuickAdd = ExtensionPreferences & {}
  /** Preferences accessible in the `groups` command */
  export type Groups = ExtensionPreferences & {}
  /** Preferences accessible in the `queue-menu` command */
  export type QueueMenu = ExtensionPreferences & {
  /** Menu Bar Title - What number to show next to the icon. The title is hidden entirely when the chosen counts are all zero. */
  "titleCounts": "running" | "running-queued" | "active" | "none",
  /** Items per Section - How many tasks to list under Running / Queued / Failed before collapsing into an "…and N more" item. Keeps a large queue from rendering hundreds of menu items every minute. */
  "maxItemsPerSection": string,
  /** Groups Section - Adds one submenu per group with pause / resume and a parallelism picker. */
  "showGroups": boolean,
  /** Menu Bar Query - Optional Pueue query applied to the menu bar's status read, e.g. "last 100". Useful when you keep thousands of finished tasks around. Leave empty for all tasks. */
  "menuQuery"?: string
}
}

declare namespace Arguments {
  /** Arguments passed to the `tasks` command */
  export type Tasks = {
  /** status=failed order_by id desc */
  "query": string
}
  /** Arguments passed to the `add-task` command */
  export type AddTask = {
  /** Command */
  "command": string
}
  /** Arguments passed to the `quick-add` command */
  export type QuickAdd = {
  /** Command */
  "command": string,
  /** Label (optional) */
  "label": string
}
  /** Arguments passed to the `groups` command */
  export type Groups = {}
  /** Arguments passed to the `queue-menu` command */
  export type QueueMenu = {}
}

