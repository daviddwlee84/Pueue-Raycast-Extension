/**
 * The Pueue wire format, 1:1 with `pueue_lib` v4.0.4's serde output.
 *
 * No I/O lives here. These types are deliberately a literal transcription of
 * the Rust structs rather than something ergonomic — the ergonomic view is
 * built on top in `normalize.ts`. Keeping the transcription honest is what
 * makes the normalizers trustworthy.
 *
 * v4.0.0 was a hard break: timestamps and results moved *into* the status
 * enum. Any v3-era example you find online has flat `start`/`end`/`result`
 * fields on Task and will not parse.
 */

/**
 * Externally tagged, and it mixes bare strings with single-field objects —
 * `"Success"` but `{"Failed": 127}`. Both shapes must be handled.
 */
export type TaskResult =
  | "Success"
  | "Killed"
  | "Errored"
  | "DependencyFailed"
  /** Non-zero exit; the number is the exit code. */
  | { Failed: number }
  /** Couldn't spawn at all — bad cwd, typo'd binary. The string is the OS error. */
  | { FailedToSpawn: string };

/**
 * Externally tagged and recursive through `Locked`. There is NO flat
 * `"status": "Running"` string anywhere in a Task.
 *
 * Note the one-letter split: `Stashed` carries `enqueue_at` (when it *will* be
 * enqueued, nullable), every other variant carries `enqueued_at` (when it
 * *was*). Getting this wrong fails silently as `undefined`.
 */
export type TaskStatus =
  | { Stashed: { enqueue_at: string | null } }
  | { Queued: { enqueued_at: string } }
  | { Running: { enqueued_at: string; start: string } }
  | { Paused: { enqueued_at: string; start: string } }
  /** Set while `pueue edit` holds the task. Wraps the status it will return to. */
  | { Locked: { previous_status: TaskStatus } }
  | {
      Done: {
        enqueued_at: string;
        start: string;
        end: string;
        result: TaskResult;
      };
    };

/** Exactly what `status --json` puts in `tasks["<id>"]`. */
export interface RawTask {
  id: number;
  created_at: string;
  original_command: string;
  command: string;
  path: string;
  /**
   * A full snapshot of the submitting shell's environment. Upstream cites
   * ~2 MB of state for 400 tasks, virtually all of it this field, and it may
   * contain secrets. Dropped at the parse boundary — see `Task`.
   */
  envs: Record<string, string>;
  group: string;
  dependencies: number[];
  priority: number;
  label: string | null;
  status: TaskStatus;
}

/**
 * The app-facing task. `envs` is dropped in `cli-transport.ts` before anything
 * can render it or write it to Raycast's disk-backed cache. Fetch it on demand
 * with `taskEnvs(id)` if a view genuinely needs it.
 */
export type Task = Omit<RawTask, "envs">;

export type GroupStatus = "Running" | "Paused" | "Reset";

export interface Group {
  status: GroupStatus;
  /** 0 means unlimited. */
  parallel_tasks: number;
}

/** `status --json` top level. Task ids are the map keys, as strings. */
export interface State {
  tasks: Record<string, Task>;
  groups: Record<string, Group>;
}

/**
 * `group --json` returns the INNER map only — `{"default": {...}}`, NOT
 * `{"groups": {...}}`. A different top-level shape from `status --json`;
 * feeding one parser to both is the easiest bug in this codebase.
 */
export type GroupMap = Record<string, Group>;

/** `log --json` → `{"<id>": {task, output}}`. Pueue blanks `task.envs` here. */
export interface LogEntry {
  task: Task;
  output: string;
}

export type LogMap = Record<string, LogEntry>;

/**
 * A timestamped read. The menu bar keeps data and its age in one cache entry
 * so the two can never drift apart.
 */
export interface Snapshot {
  state: State;
  fetchedAt: number;
}
