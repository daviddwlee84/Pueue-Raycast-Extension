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
  cleanLogOutput,
  durationMs,
  endedAt,
  enqueuedAt,
  exitCode,
  hasEverRun,
  isActive,
  isFailed,
  isLocked,
  isPueueErrorOutput,
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
import stderrFixture from "./fixtures/stderr.json";
import { argvFor, longFlagsOf, subcommandOf } from "./pueue/argv";
import type { Mutation } from "./pueue/transport";
import {
  classify,
  cleanStderr,
  firstLine,
  fromExecError,
  isDaemonDown,
  PueueError,
  type PueueErrorKind,
} from "./pueue/errors";
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";

/**
 * The live `--help` check needs a real binary. binary.ts can't be imported here
 * because it pulls in @raycast/api, which only exists inside Raycast — so probe
 * the same directories it does.
 */
const PUEUE_BIN =
  ["/opt/homebrew/bin/pueue", "/usr/local/bin/pueue"].find(existsSync) ??
  "pueue";

const state = fixture as unknown as State;
const t = (id: number) => state.tasks[String(id)];

const stderrCases = stderrFixture as {
  name: string;
  exitCode: number;
  stderr: string;
}[];

/** What each captured failure must bucket into. Wrong bucket = wrong onboarding view. */
const expectedKinds: Record<string, PueueErrorKind> = {
  "daemon-never-started": "daemon-not-running",
  "config-missing": "config-missing",
  "clap-bad-argument": "bad-arguments",
  "bad-query": "bad-query",
  "plain-client-error": "command-failed",
  "socket-absent": "daemon-not-running",
  "socket-refused": "daemon-not-running",
};

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

console.log("\nstderr normalization (fixtures captured from pueue 4.0.4)");
for (const c of stderrCases) {
  const detail = cleanStderr(c.stderr);
  check(`${c.name}: no ANSI escapes survive`, detail.includes("\u001b"), false);
  check(`${c.name}: no Location: block`, /Location:/.test(detail), false);
  check(
    `${c.name}: no backtrace hints`,
    /RUST_BACKTRACE|Backtrace omitted/.test(detail),
    false,
  );
  check(
    `${c.name}: classified`,
    classify(detail, c.exitCode),
    expectedKinds[c.name],
  );
}

const byName = (n: string) => stderrCases.find((c) => c.name === n)!;

check(
  "config-missing collapses eyre's cause chain into plain lines",
  cleanStderr(byName("config-missing").stderr),
  'Failed to read configuration.\nI/O error at path "/tmp/pueue-cap/nope.yml" while opening config file:\n      No such file or directory (os error 2)',
);
check(
  "socket-refused keeps the OS error that distinguishes it",
  /Connection refused \(os error 61\)/.test(
    cleanStderr(byName("socket-refused").stderr),
  ),
  true,
);
check(
  "socket-absent and socket-refused classify the same",
  classify(cleanStderr(byName("socket-absent").stderr), 1) ===
    classify(cleanStderr(byName("socket-refused").stderr), 1),
  true,
);
check(
  "the pest diagnostic gutter survives — its alignment is the whole message",
  cleanStderr(byName("bad-query").stderr).includes("1 | status=Nope"),
  true,
);
check(
  "bad-query keeps the list of accepted tokens",
  /expected status_queued, status_stashed/.test(
    cleanStderr(byName("bad-query").stderr),
  ),
  true,
);
check(
  "the 'Pueue: ' prefix is dropped from plain client errors",
  cleanStderr(byName("plain-client-error").stderr),
  "The task to be followed doesn't exist.",
);
check(
  "clap output is left alone apart from trimming",
  cleanStderr(byName("clap-bad-argument").stderr).startsWith(
    "error: unexpected argument '--color' found",
  ),
  true,
);
check(
  "firstLine gives a toast-sized title",
  firstLine(cleanStderr(byName("socket-absent").stderr)),
  "Failed to initialize client.",
);

console.log("\nerror predicates");
check(
  "a missing binary is not a daemon problem",
  isDaemonDown(new PueueError("binary-not-found", "x")),
  false,
);
check(
  "both daemon shapes read as down",
  [
    isDaemonDown(new PueueError("daemon-not-running", "x")),
    isDaemonDown(new PueueError("config-missing", "x")),
  ],
  [true, true],
);
check(
  "ENOENT on spawn means the binary, not the daemon",
  fromExecError({ code: "ENOENT" }, ["pueue"]).kind,
  "binary-not-found",
);
check(
  "an execFile timeout is reported as a timeout, not a command failure",
  fromExecError({ killed: true, signal: "SIGTERM", stderr: "" }, ["pueue"])
    .kind,
  "timeout",
);
check(
  "a real failure is classified from its stderr",
  fromExecError({ code: 1, stderr: byName("socket-refused").stderr }, [
    "pueue",
    "status",
  ]).kind,
  "daemon-not-running",
);
check(
  "PueueError.message is the first line, not the whole report",
  fromExecError({ code: 1, stderr: byName("config-missing").stderr }, [])
    .message,
  "Failed to read configuration.",
);

/* -- argv, checked against the real binary's --help ----------------------- */

/**
 * One representative Mutation per variant, with every optional field set so
 * that every flag we can emit gets exercised.
 */
const MUTATIONS: Mutation[] = [
  {
    op: "add",
    command: "echo hi && ls",
    group: "g",
    label: "l",
    priority: 3,
    workingDirectory: "/tmp",
    after: [1, 2],
    delay: "2h",
    stashed: true,
    immediate: true,
    escape: true,
  },
  { op: "start", ids: [1], group: "g", all: true },
  { op: "pause", ids: [1], group: "g", all: true, wait: true },
  { op: "kill", ids: [1], group: "g", all: true, signal: "int" },
  { op: "restart", ids: [1], inPlace: true, stashed: true, immediate: true },
  { op: "restart", ids: [1], inPlace: false },
  { op: "remove", ids: [1, 2] },
  { op: "stash", ids: [1], group: "g", delay: "1h" },
  { op: "enqueue", ids: [1], group: "g", delay: "1h" },
  { op: "clean", group: "g", successfulOnly: true },
  { op: "switch", a: 1, b: 2 },
  { op: "parallel", count: 4, group: "g" },
  { op: "group-add", name: "g", parallel: 2 },
  { op: "group-remove", name: "g" },
  { op: "send", id: 1, input: "y\n" },
  { op: "reset", groups: ["g"] },
];

console.log("\nargv shape");
check(
  "add puts the command last, after --, as one element",
  argvFor({ op: "add", command: "echo a && echo b" }).slice(-3),
  ["--print-task-id", "--", "echo a && echo b"],
);
check(
  "a command is never shell-quoted — pueue hands it to sh -c itself",
  argvFor({ op: "add", command: "echo 'it works'" }).at(-1),
  "echo 'it works'",
);
check(
  "restart is explicit about in-place, since the default is the user's config",
  [
    argvFor({ op: "restart", ids: [1], inPlace: true })[1],
    argvFor({ op: "restart", ids: [1], inPlace: false })[1],
  ],
  ["--in-place", "--not-in-place"],
);
check(
  "reset always forces, since we confirm in the UI",
  argvFor({ op: "reset" }),
  ["reset", "--force"],
);
check(
  "ids are stringified and trail the flags",
  argvFor({ op: "kill", ids: [3, 4], signal: "int" }),
  ["kill", "--signal", "int", "3", "4"],
);
check(
  "group subcommands are two words",
  subcommandOf(argvFor({ op: "group-add", name: "g" })),
  ["group", "add"],
);

console.log("\nargv flags exist in pueue --help (live check)");
let helpChecked = 0;
for (const m of MUTATIONS) {
  const argv = argvFor(m);
  const sub = subcommandOf(argv);
  const flags = longFlagsOf(argv);
  let help: string;
  try {
    help = execFileSync(PUEUE_BIN, [...sub, "--help"], { encoding: "utf8" });
  } catch {
    console.log(`  skip ${sub.join(" ")} — could not run --help`);
    continue;
  }
  const missing = flags.filter((f) => !help.includes(f));
  check(`${m.op}: ${flags.join(" ") || "(no flags)"}`, missing, []);
  helpChecked += 1;
}
check("every mutation variant was checked", helpChecked, MUTATIONS.length);

console.log("\nlog output — pueue hides its own errors in the output field");
// Captured verbatim from `pueue log 1 --json` on a stashed task (exit code 0).
const PUEUE_ERROR_OUTPUT =
  '(Pueue error) Failed to get log file handle: I/O error at path "/Users/david/Library/Application Support/pueue/task_logs/1.log" while getting log file handle:\nNo such file or directory (os error 2)';
check(
  "a pueue error in the output field is recognised",
  isPueueErrorOutput(PUEUE_ERROR_OUTPUT),
  true,
);
check(
  "and is not passed off as task output",
  cleanLogOutput(PUEUE_ERROR_OUTPUT),
  undefined,
);
check("real output survives", cleanLogOutput("hello\nworld\n"), "hello\nworld");
check("whitespace-only output is nothing", cleanLogOutput("  \n\n"), undefined);
check("empty output is nothing", cleanLogOutput(""), undefined);
check("undefined output is nothing", cleanLogOutput(undefined), undefined);
check(
  "output that merely mentions the phrase is kept",
  cleanLogOutput("building (Pueue error) handling\n"),
  "building (Pueue error) handling",
);
check("a stashed task cannot have a log", hasEverRun(t(0)), false);
check("a queued task cannot have a log", hasEverRun(t(2)), false);
check("a running task can", hasEverRun(t(3)), true);
check("a finished task can, even through a lock", hasEverRun(t(5)), true);

console.log(
  failures === 0
    ? "\nall assertions passed\n"
    : `\n${failures} assertion(s) FAILED\n`,
);
process.exitCode = failures === 0 ? 0 : 1;
