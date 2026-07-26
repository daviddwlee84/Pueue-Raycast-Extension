/**
 * Queue a command straight from root search, with no UI at all.
 *
 * The highest value-per-line command in the extension: type the command as an
 * argument, press return, done. Group and working directory come from whatever
 * the Add Task form last used, so the common case needs no configuration.
 *
 * `no-view` means there is no window to show a toast in, so feedback is a HUD —
 * `showToast` degrades to one automatically, but calling `showHUD` directly
 * makes that explicit.
 */

import {
  LaunchType,
  LocalStorage,
  launchCommand,
  showHUD,
  type LaunchProps,
} from "@raycast/api";
import { homedir } from "node:os";

import { describeError } from "./lib/error-states";
import { firstLine, mutate, oneline, PueueError } from "./lib/pueue";

export default async function Command(
  props: LaunchProps<{ arguments: Arguments.QuickAdd }>,
) {
  const command = (props.arguments?.command ?? "").trim();
  const label = (props.arguments?.label ?? "").trim();

  if (!command) {
    await showHUD("Nothing to queue");
    return;
  }

  const group = (await LocalStorage.getItem<string>("add.group")) ?? "default";
  const cwd = (await LocalStorage.getItem<string>("add.cwd")) ?? homedir();

  try {
    const id = await mutate({
      op: "add",
      command,
      group: group === "default" ? undefined : group,
      label: label || undefined,
      workingDirectory: cwd,
    });

    await showHUD(
      id === undefined
        ? `Queued ${oneline(command, 40)}`
        : `Queued task ${id} · ${oneline(command, 40)}`,
    );

    // Nudge the menu bar rather than leaving it up to a minute out of date.
    launchCommand({ name: "queue-menu", type: LaunchType.Background }).catch(
      () => {},
    );
  } catch (error) {
    // With no window there is nowhere to put install instructions, so the HUD
    // carries the shortest useful version of them.
    const described = describeError(error);
    const detail = error instanceof PueueError ? firstLine(error.detail) : "";
    await showHUD(
      described.structural
        ? `${described.shortTitle} — ${detail}`
        : detail || described.shortTitle,
    );
  }
}
