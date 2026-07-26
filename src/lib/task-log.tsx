/**
 * Full-page task output, static and live.
 *
 * Three sources, one per situation — see `docs/pueue-json-contract.md`:
 *
 *   detail preview   `pueue log --json --lines N`   bounded, cheap
 *   full page        the on-disk log, tailed        no JSON escaping, no RAM warning
 *   live tail        `pueue follow`                 the only streaming surface pueue has
 */

import { useEffect, useRef, useState } from "react";
import {
  Action,
  ActionPanel,
  Color,
  Detail,
  Icon,
  Keyboard,
} from "@raycast/api";
import { useCachedPromise } from "@raycast/utils";

import { ErrorDetail } from "./error-states";
import { statusTag } from "./format";
import {
  follow,
  hasEverRun,
  isRunning,
  readLogText,
  taskLogPath,
  oneline,
  type Task,
} from "./pueue";

/**
 * Program output is not markdown, so it goes in a fence — otherwise a build log
 * full of `#` and `*` renders as headings and bullets. A fence inside the output
 * would close ours early, so the backticks are broken with a zero-width space.
 */
function asCodeBlock(text: string): string {
  return ["```text", text.replace(/```/g, "``​`"), "```"].join("\n");
}

// Typed as an element rather than ReactNode: @raycast/api bundles its own
// copy of @types/react, so the root React.ReactNode is a structurally
// different type and won't assign to ActionPanel's children.
function logActions(task: Task, extra?: React.JSX.Element | null) {
  const path = taskLogPath(task.id);
  return (
    <ActionPanel>
      {extra}
      <Action.CopyToClipboard title="Copy Command" content={task.command} />
      <Action.CopyToClipboard
        title="Copy Follow Command"
        content={`pueue follow ${task.id}`}
        shortcut={{ modifiers: ["cmd", "shift"], key: "f" }}
      />
      {path ? (
        <>
          <Action.ShowInFinder title="Show Log File in Finder" path={path} />
          <Action.CopyToClipboard title="Copy Log File Path" content={path} />
        </>
      ) : null}
    </ActionPanel>
  );
}

/* -- static ---------------------------------------------------------------- */

export function TaskLogView({ task }: { task: Task }) {
  const [full, setFull] = useState(false);

  const log = useCachedPromise(
    (id: number, wantFull: boolean) =>
      readLogText(id, wantFull ? { full: true } : { lines: 200 }),
    [task.id, full],
    { execute: hasEverRun(task), keepPreviousData: true },
  );

  if (log.error)
    return <ErrorDetail error={log.error} onRetry={log.revalidate} />;

  const body = !hasEverRun(task)
    ? "_This task hasn't run yet, so it has no output._"
    : log.data
      ? asCodeBlock(log.data.text)
      : log.isLoading
        ? "_Loading…_"
        : "_No output._";

  const markdown = [
    `## Task ${task.id}`,
    "",
    asCodeBlockSh(task.command),
    "",
    log.data?.truncated
      ? "_Showing the tail. Press ⌘F for the whole log._"
      : "",
    "",
    body,
  ]
    .filter((line, i, all) => !(line === "" && all[i - 1] === ""))
    .join("\n");

  return (
    <Detail
      isLoading={log.isLoading}
      navigationTitle={`Task ${task.id} · ${statusTag(task)}`}
      markdown={markdown}
      actions={logActions(
        task,
        <>
          {log.data ? (
            <Action.CopyToClipboard
              title="Copy Output"
              content={log.data.text}
            />
          ) : null}
          {!full ? (
            <Action
              title="Show Full Log"
              icon={Icon.Text}
              shortcut={{ modifiers: ["cmd"], key: "f" }}
              onAction={() => setFull(true)}
            />
          ) : null}
          <Action
            title="Reload"
            icon={Icon.ArrowClockwise}
            shortcut={Keyboard.Shortcut.Common.Refresh}
            onAction={log.revalidate}
          />
        </>,
      )}
    />
  );
}

function asCodeBlockSh(text: string): string {
  return ["```sh", text.replace(/```/g, "``​`"), "```"].join("\n");
}

/* -- live ------------------------------------------------------------------ */

/**
 * `pueue follow` polls the on-disk log every 250 ms and exits by itself when
 * the task stops, so a render per chunk would be four re-renders a second for
 * no benefit. Accumulate in a ref and flush on a timer instead.
 */
const FLUSH_MS = 200;

/**
 * A long-running task's output grows without bound, and every character of it
 * would sit in React state. Keep a window; the beginning of a 200 MB build log
 * is not what you opened a live tail to read.
 */
const MAX_BUFFER_CHARS = 200_000;

export function TaskFollowView({ task }: { task: Task }) {
  const [text, setText] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [finished, setFinished] = useState(false);
  const [error, setError] = useState<Error | undefined>();
  const buffer = useRef("");

  useEffect(() => {
    buffer.current = "";
    setText("");
    setFinished(false);
    setIsLoading(true);

    const flush = setInterval(() => setText(buffer.current), FLUSH_MS);

    const cancel = follow(task.id, 200, {
      onData: (chunk) => {
        buffer.current += chunk;
        if (buffer.current.length > MAX_BUFFER_CHARS) {
          buffer.current =
            "…earlier output trimmed…\n" +
            buffer.current.slice(-MAX_BUFFER_CHARS);
        }
      },
      onDone: () => {
        clearInterval(flush);
        setText(buffer.current);
        setFinished(true);
        setIsLoading(false);
      },
      onError: (e) => {
        clearInterval(flush);
        setError(e);
        setIsLoading(false);
      },
    });

    // Leaving the view must kill the child, or a spawned `pueue follow` keeps
    // tailing for as long as Raycast lives.
    return () => {
      clearInterval(flush);
      cancel();
    };
  }, [task.id]);

  if (error) return <ErrorDetail error={error} />;

  const markdown = [
    `## Task ${task.id} · following`,
    "",
    asCodeBlockSh(task.command),
    "",
    text
      ? asCodeBlock(text)
      : isLoading
        ? "_Waiting for output…_"
        : "_No output._",
    "",
    // `follow` exiting is the task finishing, not a failure.
    finished ? "---\n\n_Task finished — output is complete._" : "",
  ].join("\n");

  return (
    <Detail
      isLoading={isLoading}
      navigationTitle={`Task ${task.id} · ${finished ? "finished" : "following"}`}
      markdown={markdown}
      metadata={
        <Detail.Metadata>
          <Detail.Metadata.Label title="Task" text={`#${task.id}`} />
          <Detail.Metadata.TagList title="State">
            <Detail.Metadata.TagList.Item
              text={finished ? "Finished" : "Following"}
              color={finished ? Color.Green : Color.Blue}
            />
          </Detail.Metadata.TagList>
          <Detail.Metadata.Label
            title="Command"
            text={oneline(task.command, 60)}
          />
        </Detail.Metadata>
      }
      actions={logActions(
        task,
        text ? (
          <Action.CopyToClipboard title="Copy Output" content={text} />
        ) : null,
      )}
    />
  );
}

/** True when a live tail is worth offering. */
export function canFollow(task: Task): boolean {
  return isRunning(task);
}
