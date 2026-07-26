/**
 * The seam.
 *
 * Everything above this line talks to pueue through `PueueTransport`; only
 * `cli-transport.ts` knows that pueue is a subprocess. Swapping in a CBOR
 * unix-socket transport later (see `backlog/socket-transport.md`) means
 * writing one new file and pointing the factory in `index.ts` at it.
 *
 * This module is deliberately types-only — no runtime imports — so the
 * implementation can depend on it without a cycle.
 *
 * The seam only holds because mutations are modelled as *data*. If `mutate`
 * took a `string[]` of argv, a socket transport would have to parse argv back
 * into intent — which is not a seam, it is a shell.
 */

import type { Connection } from "./connections";
import type { GroupMap, LogMap, State } from "./types";

export type Mutation =
  | {
      op: "add";
      command: string;
      group?: string;
      label?: string;
      priority?: number;
      workingDirectory?: string;
      /** Task ids this one waits on. A failed dependency yields DependencyFailed. */
      after?: number[];
      /** Seconds, a duration ("2h"), or a date expression ("wednesday 10:30pm"). */
      delay?: string;
      stashed?: boolean;
      immediate?: boolean;
      /**
       * pueue's `--escape`. Deliberately NOT exposed in the UI.
       *
       * It escapes every shell metacharacter *including spaces*, and we pass the
       * command as a single argv element, so it collapses the whole command line
       * into one token. Verified: `--escape` on `echo not-a-pipe | wc -l`
       * produces `sh: echo not-a-pipe | wc -l: command not found`. The flag is
       * only meaningful for the multi-word argv form we don't use.
       */
      escape?: boolean;
    }
  | { op: "start"; ids?: number[]; group?: string; all?: boolean }
  | {
      op: "pause";
      ids?: number[];
      group?: string;
      all?: boolean;
      wait?: boolean;
    }
  /** Note: `group` or `all` also pauses the group(s). Say so in any confirmation. */
  | {
      op: "kill";
      ids?: number[];
      group?: string;
      all?: boolean;
      signal?: string;
    }
  | {
      op: "restart";
      ids: number[];
      inPlace?: boolean;
      stashed?: boolean;
      immediate?: boolean;
    }
  /** Refuses running or paused tasks — they must be killed first. */
  | { op: "remove"; ids: number[] }
  | { op: "stash"; ids?: number[]; group?: string; delay?: string }
  | { op: "enqueue"; ids?: number[]; group?: string; delay?: string }
  | { op: "clean"; group?: string; successfulOnly?: boolean }
  | { op: "switch"; a: number; b: number }
  /** 0 means unlimited. */
  | { op: "parallel"; count: number; group?: string }
  | { op: "group-add"; name: string; parallel?: number }
  /** Moves every task in the group to `default`. Say so in any confirmation. */
  | { op: "group-remove"; name: string }
  | { op: "send"; id: number; input: string }
  | { op: "reset"; groups?: string[] };

/** Every call carries the connection it applies to; omitted means the default. */
export interface ConnectionOption {
  connection?: Connection;
}

export interface StatusOptions extends ConnectionOption {
  group?: string;
  /** The client-side query DSL, e.g. `status=failed order_by id desc first 20`. */
  query?: string;
  signal?: AbortSignal;
}

export interface LogOptions extends ConnectionOption {
  /** Trailing lines. pueue's own default is 15 when neither this nor `full` is set. */
  lines?: number;
  /** The whole file. pueue's help warns this can exhaust RAM on a big log. */
  full?: boolean;
  signal?: AbortSignal;
}

export interface FollowHandlers {
  onData(chunk: string): void;
  onDone(code: number | null): void;
  onError(e: Error): void;
}

export interface PueueTransport {
  /** `status --json` — the only call returning tasks and groups together. */
  readState(o?: StatusOptions): Promise<State>;
  /** `group --json` — a *different* top-level shape; never share a parser with readState. */
  readGroups(
    o?: ConnectionOption & { signal?: AbortSignal },
  ): Promise<GroupMap>;
  /** `log --json` — task metadata plus captured output, per id. */
  readLogs(ids: number[], o?: LogOptions): Promise<LogMap>;
  /** Exit-code-only. Returns the new task id for `add`, otherwise void. */
  mutate(m: Mutation, o?: ConnectionOption): Promise<number | void>;
  /** The only streaming surface pueue offers. Returns a cancel function. */
  followLog(
    id: number,
    lines: number,
    h: FollowHandlers,
    o?: ConnectionOption,
  ): () => void;
  /** Version and reachability, for onboarding and the v4 guard. */
  probe(
    o?: ConnectionOption,
  ): Promise<{ version: string; major: number; reachable: boolean }>;
}
