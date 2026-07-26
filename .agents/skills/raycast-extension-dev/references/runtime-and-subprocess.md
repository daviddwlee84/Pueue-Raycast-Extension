# Runtime and subprocesses

Read this when the extension shells out to a binary, resolves a path, spawns or
streams a subprocess, or reports `spawn … ENOENT` / "works in my terminal but not
from Raycast".

## Table of contents

- [The launchd environment](#the-launchd-environment)
- [Resolving a binary](#resolving-a-binary)
- [The corollary: never start a daemon from Raycast](#the-corollary-never-start-a-daemon-from-raycast)
- [Running a command](#running-a-command)
- [Streaming output](#streaming-output)
- [An error taxonomy](#an-error-taxonomy)
- [Argv discipline](#argv-discipline)
- [Node version](#node-version)

---

## The launchd environment

Raycast runs extensions in a managed Node process started by **launchd**. launchd
never sources `~/.zshrc`, `~/.zprofile`, `~/.bash_profile`, or any other shell rc,
because those are read by an interactive shell and there is no shell here.

Every `PATH` entry you rely on — `/opt/homebrew/bin`, `~/.cargo/bin`,
`~/.local/bin`, a mise or asdf shim directory — is added *by those files*. The
extension's `PATH` is roughly:

```text
/usr/bin:/bin:/usr/sbin:/sbin
```

So `execFile("pueue", …)` fails with `Error: spawn pueue ENOENT` while
`which pueue` in your terminal answers instantly.

**The dev console lies.** `ray develop` runs in the terminal you launched it
from, and inherits that terminal's full interactive environment. A resolver that
would fail in production passes there every time. Any change touching `PATH`,
`HOME`, or a config-file location must be exercised by opening the command **from
Raycast root search or the menu bar**, not from the dev console.

You can see the real environment by having a command render
`JSON.stringify(process.env, null, 2)` once, opened from root search. Do it once
per project — it takes a minute and settles every argument.

## Resolving a binary

Never invoke a bare name. Resolve an absolute path, in this order:

```ts
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const PROBE_DIRS = [
  "/opt/homebrew/bin",            // Apple Silicon Homebrew
  "/usr/local/bin",               // Intel Homebrew — and Rosetta, and MacPorts-adjacent
  join(homedir(), ".cargo", "bin"),
  join(homedir(), ".local", "bin"),
  "/usr/bin",
  "/bin",
];

let cached: string | undefined;

export function resolveBinary(name: string): string {
  const fromPref = getPreferenceValues<Preferences>().binaryPath?.trim();
  // existsSync-validate the preference so a stale value falls THROUGH to the
  // probe rather than failing forever with a path the user has since deleted.
  if (fromPref && existsSync(fromPref)) return fromPref;
  if (cached && existsSync(cached)) return cached;

  for (const dir of PROBE_DIRS) {
    const candidate = join(dir, name);
    if (existsSync(candidate)) return (cached = candidate);
  }
  throw new AppError("binary-not-found", `${name} was not found in ${PROBE_DIRS.join(", ")}`);
}
```

**Both Homebrew prefixes must be probed.** `/opt/homebrew` is Apple Silicon;
`/usr/local` is Intel, and also where an x86 Homebrew under Rosetta lands on an
Apple Silicon machine. Hardcoding either breaks half of all Macs, and you will
not notice on your own.

Ship a `binaryPath` preference whose *description* explains the trap:

> Absolute path to the binary. Raycast runs under launchd with no shell rc, so a
> bare name is never on `PATH`.

Pass `HOME` explicitly on every spawn, or a CLI that looks up
`~/.config/<tool>/` will not find its config:

```ts
const baseEnv = () => ({ ...process.env, HOME: process.env.HOME ?? homedir() });
```

## The corollary: never start a daemon from Raycast

If your extension offers a "Start the daemon" action and that action spawns the
daemon directly, the daemon becomes a launchd child of Raycast and inherits
Raycast's stripped environment — which it then hands to **every job it ever
runs**. Instead of failing loudly once, it silently poisons everything
afterwards.

Only offer a one-click start when a service manager owns the daemon
(`brew services start …`, `launchctl kickstart …`). Detect that first, and fall
back to a copy-to-clipboard action with the command the user should run in a
terminal.

## Running a command

```ts
import { execFile } from "node:child_process";
import { promisify } from "node:util";
const pexecFile = promisify(execFile);

const READ_TIMEOUT_MS = 10_000;
const BIG_BUFFER = 64 * 1024 * 1024;

async function run(argv: string[], o: { timeout?: number; signal?: AbortSignal } = {}) {
  const bin = resolveBinary("mytool");
  try {
    const { stdout } = await pexecFile(bin, argv, {
      env: baseEnv(),
      timeout: o.timeout ?? READ_TIMEOUT_MS,
      signal: o.signal,
      maxBuffer: BIG_BUFFER,
    });
    return stdout;
  } catch (e) {
    throw fromExecError(e, [bin, ...argv]);
  }
}
```

Four rules:

- **Never `shell: true`.** With an argv array nothing re-parses your quoting, and
  a user-supplied command string stays exactly one argv element. With
  `shell: true` a semicolon in a filename becomes a second command.
- **Raise `maxBuffer`.** The default is 1 MB. A read that exceeds it is
  *truncated*, so you get a JSON parse error at a random offset instead of a
  clear "output too large". Anything whose size scales with user data — a status
  dump, a log read — needs a real ceiling.
- **Always set a timeout**, and give writes a longer one than reads.
- **Funnel every call through one `run()`** so every failure classifies the same
  way. A second ad-hoc `execFile` elsewhere is how error handling drifts.

`AbortSignal` support matters because Raycast re-renders freely; see
`data-and-state.md` for wiring it to `useCachedPromise`'s `abortable`.

## Streaming output

For a follow/tail view, use `spawn` and return a cancel closure:

```ts
export function follow(argv: string[], h: Handlers): () => void {
  const child = spawn(resolveBinary("mytool"), argv, { env: baseEnv() });
  child.stdout?.setEncoding("utf8");
  child.stdout?.on("data", (d: string) => h.onData(d));
  let stderr = "";
  child.stderr?.on("data", (d: string) => (stderr += d));
  child.on("error", (e) => h.onError(e));
  child.on("close", (code) => {
    // Exiting by itself is completion, not failure. Only non-zero is an error.
    if (code && code !== 0) h.onError(fromExecError({ code, stderr }, argv));
    h.onDone(code);
  });
  return () => { if (!child.killed) child.kill("SIGTERM"); };
}
```

Consumer discipline, all three of which were learned the hard way:

- **Buffer and flush on an interval.** A `setState` per chunk is several
  re-renders a second for no benefit. Accumulate into a `useRef` and flush every
  ~200 ms.
- **Cap the buffer.** `"…earlier output trimmed…\n" + buf.slice(-200_000)`. An
  unbounded build log will make the view unusable.
- **Call the cancel closure from the `useEffect` cleanup.** Leaving the view must
  kill the child, or the subprocess tails for as long as Raycast lives.

## An error taxonomy

Classify once, at the boundary, into a discriminated union. Every UI decision
downstream keys off `kind` rather than re-reading strings.

```ts
export type ErrorKind =
  | "binary-not-found" | "daemon-not-running" | "config-missing"
  | "bad-arguments" | "bad-query" | "timeout" | "command-failed";

export class AppError extends Error {
  readonly kind: ErrorKind;
  readonly detail: string;      // the whole report, for a Detail fence
  readonly exitCode?: number;
  constructor(kind: ErrorKind, detail: string, opts?: { exitCode?: number }) {
    super(firstLine(detail) || kind);   // message stays ONE line, for a toast title
    this.kind = kind; this.detail = detail; this.exitCode = opts?.exitCode;
  }
}
```

`fromExecError` needs to know four things Node does not make obvious:

- `e.code === "ENOENT"` → `binary-not-found`.
- `e.killed && (e.signal === "SIGTERM" || e.code === "ETIMEDOUT")` → `timeout`.
  **`execFile` reports its own timeout as a SIGTERM kill**, not a distinct code.
- Exit codes are frequently 0/1/**2**, not 0/1. clap-based CLIs use 2 for usage
  errors — which are always *your* bug, so surface them verbatim rather than
  dressing them up.
- Some CLIs write ANSI escapes to stderr even when it is a pipe, ignoring both
  `--color never` and `NO_COLOR` (anything using Rust's `color_eyre` does). Strip
  them:

  ```ts
  // eslint-disable-next-line no-control-regex -- some CLIs emit raw SGR to a pipe
  const ANSI_SGR = /\[[0-9;]*m/g;
  ```

Two refinements worth stealing:

**Drop known-noise lines before taking `firstLine()`.** A per-command warning
("Different protocol version detected…") is invisible on success but lands
*first* on failure, so it becomes your toast title and hides the real error.

**Do not over-strip.** If the CLI emits an aligned diagnostic gutter for query
syntax errors, that alignment is the entire value of the message. Strip cause
numbering and backtrace hints, keep the gutter, and assert both with a fixture.

## Argv discipline

- **Global flags precede the subcommand** for anything clap-based.
  `tool status --color never` exits 2 with `error: unexpected argument`;
  `tool --color never status` is correct. Build argv as
  `[...globalArgs(), ...subcommandArgs]` — prepended, never appended.
- **A user-supplied command goes last, after `--`, as ONE argv element.** If the
  CLI joins a variadic argument and hands it to `sh -c`, quoting it yourself
  double-escapes it. Assert this:

  ```ts
  check("a command is never shell-quoted", argvFor({ op: "add", command: "echo 'hi'" }).at(-1),
        "echo 'hi'");
  ```

- **Be explicit about flags whose default comes from the user's config.** Emit
  `--in-place` or `--not-in-place`, never neither. The action should do what it
  says regardless of whose machine it runs on.
- **Assert generated argv against the CLI's own `--help` at verify time:**

  ```ts
  const help = execFileSync(bin, [sub, "--help"], { encoding: "utf8" });
  const missing = flags.filter((f) => !help.includes(f));
  check(`${sub}: ${flags.join(" ")}`, missing, []);
  ```

  A flag in the wrong place, or one that does not exist, then fails in the gate
  instead of in front of a user.

- **Never infer capability from an exit code — parse the shape.** For a version
  probe, regex the version string, and default to *current* when it does not
  parse. Blocking a user because you could not read a version banner is worse
  than optimistically proceeding.

## Node version

Raycast's runtime is **Node 22**. Pin `@types/node` to `^22`, whatever your shell
has. Typing against 24 lets you write APIs that compile locally and throw at
runtime inside Raycast, which is the most annoying possible failure mode because
the gate is green.
