/**
 * Assertions over `normalize.ts` against `fixtures/state.json`.
 *
 * There is no test runner in a Raycast extension and adding one is store
 * review noise, so this is a plain script:
 *
 *     just verify
 *
 * which compiles it with the already-installed tsc and runs it under node.
 * Every assertion here corresponds to a shape that pueue v4.0.4 actually
 * emits and that a naive reading of the JSON gets wrong.
 */

import type { State, TaskStatus } from "./pueue/types";
import {
  durationMs,
  endedAt,
  enqueuedAt,
  exitCode,
  isActive,
  isFailed,
  isLocked,
  isSuccess,
  parseTs,
  spawnError,
  startedAt,
  statusKeywords,
  statusKind,
  statusLabel,
  taskList,
  taskResult,
  resultKind,
  underlyingKind,
  unwrapLocked,
} from "./pueue/normalize";
import fixture from "./fixtures/state.json";

const state = fixture as unknown as State;
const t = (id: number) => state.tasks[String(id)];

let failures = 0;

function check(name: string, actual: unknown, expected: unknown) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a === e) {
    console.log(`  ok   ${name}`);
  } else {
    failures += 1;
    console.log(
      `  FAIL ${name}\n         expected ${e}\n         actual   ${a}`,
    );
  }
}

console.log("\nstatus enum tagging");
check("Stashed reports as stashed", statusKind(t(0).status), "stashed");
check("Queued reports as queued", statusKind(t(2).status), "queued");
check("Running reports as running", statusKind(t(3).status), "running");
check("Paused reports as paused", statusKind(t(4).status), "paused");
check("Done reports as done", statusKind(t(6).status), "done");
check(
  "Locked reports as locked at the surface",
  statusKind(t(5).status),
  "locked",
);
check(
  "an unrecognised variant degrades to unknown",
  statusKind({ Frobnicated: {} } as unknown as TaskStatus),
  "unknown",
);

console.log("\nLocked unwrapping (recursive)");
check("Locked over Done unwraps to done", underlyingKind(t(5).status), "done");
check(
  "Locked over Queued unwraps to queued",
  underlyingKind(t(11).status),
  "queued",
);
check("isLocked is true for a locked task", isLocked(t(5).status), true);
check("isLocked is false for a plain done task", isLocked(t(6).status), false);
check(
  "a doubly-locked status unwraps fully",
  statusKind(unwrapLocked({ Locked: { previous_status: t(5).status } })),
  "done",
);
check("a locked queued task still counts as active", isActive(t(11)), true);

console.log("\nTaskResult — bare strings and single-field objects");
check("Success", resultKind(taskResult(t(6).status)), "success");
check(
  "{ Failed: 127 } through a lock",
  resultKind(taskResult(t(5).status)),
  "failed",
);
check(
  "exit code is read out of { Failed: n }",
  exitCode(taskResult(t(5).status)),
  127,
);
check(
  "Success carries no exit code on the wire",
  exitCode(taskResult(t(6).status)),
  undefined,
);
check(
  "{ FailedToSpawn }",
  resultKind(taskResult(t(7).status)),
  "failed-to-spawn",
);
check(
  "FailedToSpawn keeps the OS error",
  spawnError(taskResult(t(7).status)),
  "No such file or directory (os error 2)",
);
check("Killed", resultKind(taskResult(t(8).status)), "killed");
check(
  "DependencyFailed",
  resultKind(taskResult(t(9).status)),
  "dependency-failed",
);
check("Errored", resultKind(taskResult(t(10).status)), "errored");
check(
  "an unfinished task has no result",
  resultKind(taskResult(t(3).status)),
  undefined,
);

console.log("\nfailure is an allowlist, not a denylist");
check("Success is not a failure", isFailed(t(6)), false);
check("Failed is a failure", isFailed(t(5)), true);
check("FailedToSpawn is a failure", isFailed(t(7)), true);
check("Killed is a failure", isFailed(t(8)), true);
check("DependencyFailed is a failure", isFailed(t(9)), true);
check("Errored is a failure", isFailed(t(10)), true);
check("a running task is not a failure", isFailed(t(3)), false);
check("Success is a success", isSuccess(t(6)), true);

console.log("\ntimestamps — enqueue_at vs enqueued_at, and the null trap");
check(
  "Stashed with enqueue_at null is undefined, NOT 1970",
  enqueuedAt(t(0).status),
  undefined,
);
check(
  "Stashed with a scheduled time parses",
  enqueuedAt(t(1).status)?.toISOString(),
  "2026-07-26T19:00:00.000Z",
);
check(
  "Queued reads enqueued_at",
  enqueuedAt(t(2).status)?.toISOString(),
  "2026-07-26T01:02:01.500Z",
);
check(
  "Running reads enqueued_at",
  enqueuedAt(t(3).status)?.toISOString(),
  "2026-07-26T01:03:00.500Z",
);
check(
  "Done reads enqueued_at through a lock",
  enqueuedAt(t(5).status)?.toISOString(),
  "2026-07-26T01:05:00.500Z",
);
check("a queued task has no start", startedAt(t(2).status), undefined);
check("a stashed task has no start", startedAt(t(0).status), undefined);
check(
  "Running has a start",
  startedAt(t(3).status)?.toISOString(),
  "2026-07-26T01:03:01.000Z",
);
check("only Done has an end", endedAt(t(3).status), undefined);
check(
  "Done end parses through a lock",
  endedAt(t(5).status)?.toISOString(),
  "2026-07-26T01:05:31.000Z",
);
// chrono emits 6 fractional digits; V8 accepts them and truncates to ms.
check(
  "microsecond precision truncates to ms rather than failing",
  parseTs("2026-04-27T11:01:06.893055+08:00")?.getTime(),
  Date.UTC(2026, 3, 27, 3, 1, 6, 893),
);
check(
  "a numeric offset is honoured, not treated as UTC",
  parseTs("2026-04-27T11:01:06.000000+08:00")?.toISOString(),
  "2026-04-27T03:01:06.000Z",
);
check(
  "garbage is undefined rather than Invalid Date",
  parseTs("not a date"),
  undefined,
);

console.log("\nduration");
check("finished duration is end-start", durationMs(t(5)), 30_000);
check(
  "running duration is now-start",
  durationMs(t(3), Date.parse("2026-07-26T09:03:11.000+08:00")),
  10_000,
);
check("a queued task has no duration", durationMs(t(2)), undefined);

console.log("\nlabels and keywords");
check("running", statusLabel(t(3)), "running");
check(
  "failed carries the exit code and the edit lock",
  statusLabel(t(5)),
  "failed (127) (editing)",
);
check("plain stashed", statusLabel(t(0)), "stashed");
check("scheduled stashed", statusLabel(t(1)), "scheduled");
check("failed to spawn has no exit code", statusLabel(t(7)), "failed to spawn");
check("dependency failed", statusLabel(t(9)), "dependency failed");
check(
  "keywords include the exit code",
  statusKeywords(t(5)).includes("127"),
  true,
);
check(
  "keywords drop a null label rather than emitting empty",
  statusKeywords(t(0)).includes(""),
  false,
);

console.log("\ncollection helpers");
check(
  "taskList sorts numerically, not lexically",
  taskList(state.tasks).map((x) => x.id),
  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
);

console.log(
  failures === 0
    ? "\nall assertions passed\n"
    : `\n${failures} assertion(s) FAILED\n`,
);
process.exitCode = failures === 0 ? 0 : 1;
