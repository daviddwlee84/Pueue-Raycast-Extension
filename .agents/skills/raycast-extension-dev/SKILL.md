---
name: raycast-extension-dev
description: 'Build, verify, and ship Raycast extensions in TypeScript with @raycast/api. Use when the user says "Raycast extension", "ray build", "ray develop", "ray lint", "menu bar command", "MenuBarExtra", "useCachedPromise", "raycast-env.d.ts", "no-view command", or wants to publish to the Raycast Store. Covers the launchd PATH trap, the typecheck ray build skips, optimistic-update reconcile timing, and the store-readiness checks ray lint never runs.'
---

# Raycast extension development

A Raycast extension is a manifest (`package.json`) plus React components, bundled
by esbuild and run inside Raycast's own Node 22 process — which launchd starts,
so it has none of your shell environment. The API surface is well documented at
<https://developers.raycast.com>. Read that for what `List` and `Form` accept.

This skill is the other 20%: what the toolchain does **not** check, what the
runtime does **not** provide, and what the store checks that the linter does not.
Every claim here was paid for once in a shipped extension; the `## Gotchas`
section is the compressed bill.

macOS only. `platforms`, `sips`, Homebrew prefixes, and launchd are all
macOS-shaped, and a `menu-bar` command does not exist anywhere else.

## When to use

- "Build me a Raycast extension for X" → Workflow A, then B if X is a CLI.
- "Wrap this CLI / API in Raycast" → Workflow B. The seam design is the decision
  that matters; everything else is recoverable.
- "It works in `npm run dev` but not from Raycast" → the launchd PATH gotcha.
  This is the most common report and the dev terminal cannot reproduce it.
- "The menu bar shows the wrong count / nothing / stale data" → Workflow E.
- "My action flashes the new state, reverts, then re-applies" → Workflow C.
- "Get this ready for the Raycast Store" → Workflow F and
  `scripts/check-store-readiness.sh`.
- Reviewing an existing extension: run the gate (Workflow A) first. It usually
  finds something, because `ray build` alone never would have.

## When NOT to use

- **Raycast Script Commands** — shell scripts in a different repo, no manifest,
  no React, no `@raycast/api`. Entirely separate surface.
- **Generic React/TSX quality** (hook rules, component structure, a11y) → the
  `react-best-practices` skill is the better authority. This skill deliberately
  restates none of it.
- **The semantics of the CLI being wrapped** — `pueue`'s flags, `gh`'s JSON —
  belong to that tool's own skill or its `--help`.
- **AI Extensions (`tools[]`) when the user has no Raycast Pro.** They are
  Pro-gated, so they can never be the primary UX. And there is no `confirmAlert`
  equivalent inside a tool call, so a first version must be read-only.
- **Taking the store screenshots.** This skill can verify their count and
  dimensions. It cannot capture a window, and no CLI can — see Workflow F.

## The gate

`ray build` — the default `-e dev` build, which is what `npm run build`,
`just build`, and `ray develop` all run — proves only that a bundle exists.

| Command | Catches | Misses |
| --- | --- | --- |
| `tsc --noEmit` | types | manifest, formatting, runtime shape |
| `node dev-check.js` | your own invariants, wire shapes, generated argv | anything you did not assert |
| `ray lint` | manifest schema, icons, ESLint, Prettier, **reserved-shortcut collisions**, and — **only under `CI=true`** — `package-lock.json` registry hosts | types |
| `ray build` (`-e dev`) | syntax, that esbuild can bundle it | **types — esbuild strips them without checking** |
| `ray build -e dist` | the above **plus types** — it shells out to `tsc -p tsconfig.json --noEmit` | manifest, formatting |

Measured on a scaffold with one genuine `TS2345`: `ray build` exits **0** and
prints `ready - built extension successfully`; `ray build -e dist` exits **1**
and reports the error. So the dist build is a stronger pre-submit check than it
looks — but it is not the build you run all day, which is exactly why the type
error survives long enough to ship.

All four, in that order, every time:

```make
typecheck: npx tsc --noEmit -p tsconfig.json
verify:    npx tsc --outDir .build/verify --module commonjs --target ES2022 \
             --lib ES2023 --esModuleInterop --strict src/lib/dev-check.ts \
             && node .build/verify/dev-check.js
check: typecheck verify lint build
```

`assets/Justfile.template` is this, complete, plus a `dist` recipe. Two real bugs
shipped past `ray build` in the source extension: a `MutatePromise<GroupMap>`
passed where `MutatePromise<State>` was expected (it would have emptied the list
on every action), and a `ReactNode`/`JSX.Element` clash. Neither printed a line.

## Surface map

The complete list of extension entry points. There is nothing else.

| Entry point | Manifest | Notes |
| --- | --- | --- |
| View command | `"mode": "view"` | pushes a view onto the navigation stack |
| No-view command | `"mode": "no-view"` | runs and exits; **exports `async function Command(props)`, not a component** |
| Menu bar command | `"mode": "menu-bar"` | returns a `MenuBarExtra`. **macOS only** |
| AI tools | `"tools": [...]` | invoked by Raycast AI. **Pro-gated** |
| Script commands | separate repo | shell scripts, not extensions |

**There is no widget API** — no Notification Center, Control Center, or
Live-Activity surface. A `menu-bar` command is the only way to show something
without opening Raycast, and that constrains the whole design.

## Repo layout

**The extension IS the repo root.** Not a subdirectory: `ray build`, `develop`,
and `lint` all resolve `./package.json` and write `./raycast-env.d.ts` relative
to cwd, and `ray publish` copies the extension directory *verbatim* into
`extensions/<name>/` of the review PR.

```text
package.json          the manifest — commands, preferences, arguments
raycast-env.d.ts      GENERATED by ray build. Commit it.
assets/               extension-icon.png (512x512) + monochrome menu-bar SVG
metadata/             3-6 store screenshots at 2000x1250
src/<command>.tsx     one file per commands[].name — the names must match
src/lib/              shared code
src/lib/<backend>/    the transport seam (Workflow B)
```

Consequence of the verbatim copy: anything else in the root — notes, task lists,
a docs site, a `.venv` — rides along into the review PR. Use an allowlist export
to a scratch directory if that bothers you; a `.gitignore` will not help, because
`publish` copies the directory, not the git index.

## Workflow A — scaffold or retrofit

```bash
bash scripts/new-raycast-extension.sh --dir ./my-ext --name my-ext \
  --author <your-raycast-username> --command tasks:view --command menu:menu-bar
cd my-ext && npm install
npm run build          # this is what GENERATES raycast-env.d.ts
just check             # the four-stage gate
```

For an existing extension, skip the script and copy `assets/Justfile.template`,
`assets/tsconfig.json.template`, and `assets/dev-check.ts.template` in. The one
non-obvious line is `"include": ["src/**/*", "raycast-env.d.ts"]` — omit it and
the generated `Preferences` globals are invisible to `tsc`.

Then **open the command from Raycast root search**, not from the `ray develop`
console. The console inherits your interactive environment; Raycast does not.
Anything environment-dependent that you only tested in the terminal is untested.

## Workflow B — wrap a CLI behind a transport seam

Model every mutation as a **data union**, never as argv strings:

```ts
export type Mutation =
  | { op: "add"; command: string; group?: string; label?: string }
  | { op: "kill"; ids?: number[]; group?: string };

export interface Transport {
  read(o?: ReadOptions): Promise<State>;
  mutate(m: Mutation, o?: Options): Promise<number | void>;
}
```

If `mutate` took a `string[]` of argv, a future socket or HTTP transport would
have to parse argv back into intent — that is not a seam, it is a shell.
`assets/transport.ts.template` is the skeleton, with the argv builder as a pure
function so `dev-check` can assert it without spawning anything.

Rules that are not negotiable:

- **Resolve an absolute binary path.** Preference first, `existsSync`-validated so
  a stale value falls through, then probe **both** `/opt/homebrew/bin` and
  `/usr/local/bin`, then `~/.cargo/bin`, `~/.local/bin`, `/usr/bin`, `/bin`.
- **`execFile` with an argv array. Never `shell: true`.** Nothing re-parses your
  quoting, and a user-supplied command stays one argv element.
- **Raise `maxBuffer`.** The 1 MB default turns a large JSON read into a parse
  error rather than a clear failure. 64 MB is a reasonable ceiling.
- **Set a timeout, and know that `execFile` reports its own timeout as a SIGTERM
  kill**, not a distinct code.
- **Classify stderr into a discriminated `kind`** — `binary-not-found`,
  `daemon-not-running`, `bad-arguments`, `timeout`, `command-failed` — and keep
  the full text on the error for a `Detail` fence while the `message` stays one
  line for a toast title.
- **Strip secret-bearing fields at the parse boundary** (Workflow C explains why).
- **Global flags precede the subcommand** for clap-based CLIs.

Read `references/runtime-and-subprocess.md` **when** the extension shells out,
resolves a path, streams output, or reports `spawn … ENOENT`.

## Workflow C — data, cache, and mutations

```ts
const state = useCachedPromise(
  (group: string, account: string) => read({ group, account }),
  [group, account],                       // <-- arguments, not closure captures
  { keepPreviousData: true, abortable: stateAbort },
);
```

- **Everything the fetcher depends on must be an argument.** The argument array
  is what keys the cache and triggers refetch. A closed-over variable changes
  silently and keeps serving the previous account's data.
- **One `abortable` ref per independent read**, or a superseded read cancels an
  unrelated in-flight one.
- **`execute: false` for conditional fetches**, and `keepPreviousData: false`
  when the value is per-selection.
- **`useCachedPromise` writes to a disk-backed `Cache`.** Every field of the
  resolved value lands on disk in plaintext. Strip environment snapshots, tokens,
  and auth headers in the parse function, not at render time.

For mutations, the default `mutate()` revalidates on success — which is wrong for
any backend that acknowledges before it applies:

```ts
await state.mutate(run(mutation), {
  optimisticUpdate: (data) => predict(data, mutation),
  rollbackOnError: true,
  shouldRevalidateAfter: false,          // the load-bearing line
});
setTimeout(() => state.revalidate(), FAST_MS);
setTimeout(() => state.revalidate(), SETTLE_MS);
```

Measured on one such daemon: ack in ~22 ms, applied ~280 ms later, so the
built-in revalidate returned pre-change state and visibly undid the action.
`400` and `1500` worked there. **Measure your own backend** — those two numbers
are a worked example, not a constant.

And an optimistic updater must **return its input unchanged when the outcome is
unknowable**. A new server-assigned id, a scheduler decision, a reordering: a
wrong prediction flickers exactly like no prediction, and is also wrong.

Read `references/data-and-state.md` **when** wiring `useCachedPromise`/`mutate`,
when an action reverts and re-applies, or when deciding what to persist and under
which key.

## Workflow D — failure states as data

One descriptor, N renderers. The alternative — an `errorMarkdown()` helper per
surface — drifts, and the menu bar cannot render a `List.EmptyView` anyway.

```ts
export interface ErrorDescriptor {
  icon: Image.ImageLike;
  title: string;
  shortTitle: string;    // a few words — the list column is a third of the window
  description: string;   // one line, for List.EmptyView
  markdown: string;      // a full page, for Detail
  actions: ErrorAction[];// DATA: { id, title, icon, copy? | url? | run? }
  structural: boolean;   // is the backend gone, or was this read just flaky?
}
```

`structural` is the decision that matters. `useCachedPromise` keeps serving its
last good result when a fetch fails — correct for a flaky read, wrong when the
backend is gone, because then the cached list renders a dead system as live.

| Situation | Render |
| --- | --- |
| first read failed, no data | `<List><ErrorEmptyView/></List>` — the whole screen |
| structural failure, cached data present | a persistent banner row atop the list |
| transient failure | an ordinary toast |

Actions stay data so every renderer maps them to its own primitive — `Action` in
a panel, a `Clipboard.copy` call in the menu bar.
`assets/error-descriptor.tsx.template` has all four renderers.

## Workflow E — the menu bar

```tsx
<MenuBarExtra icon={icon} title={n > 0 ? String(n) : undefined} isLoading={isLoading}>
```

- **There is no badge API.** The count *is* the `title`, and `undefined` is what
  removes it.
- **`isLoading` is a contract, not a hint.** Never set: Raycast renders then
  immediately unloads. Stuck true: the whole tree re-runs every tick.
- **`interval` is manifest-only** — a preference cannot change it, and Raycast
  renders its own control. Use `"1m"`; the docs contradict themselves on the floor.
- **Background refresh is off by default for store installs.** Until the user runs
  the command once, a fresh install shows nothing. Put this at the top of the README.
- **Raycast restores the item from a database on restart**, not by re-running the
  command, so a stale render survives invisibly. Show an `Updated HH:MM` row.
- **`confirmAlert` cannot present here** — it renders in the Raycast window, which
  is closed while the menu is open. Destructive items go behind `alternate` (⌥),
  and the truly dangerous ones are not offered at all.
- **Two identical items at the same level cross their `onAction` handlers.** Prefix
  every row with its id.
- **Cap the item count.** 400 rows must not become 400 menu items every minute.
- **Never return `null` on error** — that removes the item entirely from someone
  who deliberately enabled it. Render an error menu instead.
- Feedback is `showHUD`, not `showToast`. There is no window.

Read `references/menu-bar.md` **when** the extension has or is gaining a
`mode: "menu-bar"` command, or the menu bar shows stale data, a wrong count, or
nothing at all.

## Workflow F — ship to the store

```text
[ ] author is your REGISTERED Raycast username
[ ] license: "MIT" AND an MIT LICENSE file
[ ] at least one category; platforms: ["macOS"] if any command is menu-bar
[ ] package-lock.json committed — npm ci must work from a clean checkout
[ ] assets/extension-icon.png at exactly 512x512, readable light and dark
[ ] a monochrome template SVG for the menu bar, tinted at runtime
[ ] CHANGELOG.md opening with `## [Initial Version] - {PR_MERGE_DATE}`
[ ] README covering setup, and any background-refresh default
[ ] 3-6 PNGs at exactly 2000x1250 in metadata/
[ ] tsc --noEmit, ray lint, and ray build -e dist all clean
```

Then `bash scripts/check-store-readiness.sh .` — because **`ray lint` exits 0
with a completely empty `metadata/`**. Screenshot count and dimensions, icon
size, the CHANGELOG placeholder, and a real `author` are review-time
requirements the linter never runs.

Three steps cannot be automated, and pretending otherwise wastes an afternoon:

1. **Screenshots.** Raycast's Window Capture, triggered by a hotkey with
   "Save to Metadata" ticked. That option only appears once a `metadata/` folder
   exists, so create it (with a README inside) before you start.
2. **`ray login`** is a browser OAuth flow.
3. **`npx @raycast/api@latest publish`** opens a pull request against
   `raycast/extensions`. CI runs, then a human reviews. Days, not seconds.

Note `ray build` defaults to `-e dev`. Run `ray build -e dist` at least once
before submitting — it is the build the store actually produces.

Read `references/store-publishing.md` **when** the user asks to publish or make
something store-ready, or when `check-store-readiness.sh` fails and you want the
reasoning behind a check.

## Available scripts

- **`scripts/new-raycast-extension.sh`** — scaffold a gated extension: manifest,
  tsconfig, flat ESLint config, Justfile gate, `dev-check` harness, one stub per
  command, placeholder icon.
  - Flags: `--dir DIR --name NAME [--title T] [--author U]
    [--command NAME:MODE[:TITLE]]… [--license SPDX] [--no-verify-harness]
    [--dry-run] [--force] [--json] [--help]`
  - Exit: `0` written · `1` bad args · `2` target not empty · `3` bundled assets
    missing · `4` post-write self-check failed
  - Does not run `npm install`. It prints the ordered next steps instead.
- **`scripts/check-store-readiness.sh`** — the store preconditions `ray lint`
  does not check. JSON array of `{id, status, detail, fix}` on stdout.
  - Flags: `[DIR] [--json] [--quiet] [--strict] [--help]`
  - Exit: `0` all pass · `1` bad args · `2` not an extension directory · `3` a
    required tool is missing (check skipped, **not** passed) · `4` a check failed

Deliberately absent, so nobody re-adds them: a wrapper around
`tsc && ray lint && ray build` (that is a four-line Justfile recipe, already an
asset); a PATH-probe script (it would run in *your* shell — the one environment
that hides the bug); a manifest validator (`ray lint` already validates against
the published schema, and a copy would drift).

## Bundled assets

| Asset | Use |
| --- | --- |
| `Justfile.template` | the four-stage gate, plus the `([ -d node_modules ] \|\| npm install) &&` self-bootstrap every recipe needs, because `ray` ships inside `@raycast/api` |
| `tsconfig.json.template` | working config; note `include` must list `raycast-env.d.ts` |
| `eslint.config.mjs.template` | the flat-config incantation for `@raycast/eslint-config` v2 |
| `package.json.template` | manifest skeleton with a known-good dependency set |
| `dev-check.ts.template` | the no-test-runner harness, with the argv-vs-`--help` cross-check |
| `transport.ts.template` | the `Mutation` data-union seam |
| `error-descriptor.tsx.template` | `ErrorDescriptor` and its four renderers |
| `metadata-README.md.template` | drop into `metadata/` so "Save to Metadata" appears |
| `extension-icon.placeholder.png` | a real 512x512 so a fresh scaffold passes `ray lint` — and its sha256 is what `check-store-readiness.sh` fails on |

## Reference files

Load one only when its condition fires. Do not preload.

| Reference | Read it **when** |
| --- | --- |
| `references/runtime-and-subprocess.md` | the extension shells out, resolves a binary path, spawns or streams a subprocess, or reports `spawn … ENOENT` / "works in my terminal but not from Raycast" |
| `references/manifest-and-commands.md` | adding or renaming a command, adding preferences or arguments, wiring one command to launch another, or considering AI tools |
| `references/data-and-state.md` | wiring `useCachedPromise`/`usePromise`/`mutate`, an action visibly reverts and re-applies, or deciding what to persist and under which key |
| `references/ui-patterns.md` | building a List/Detail/Form surface, adding a dropdown or shortcut, rendering program output as markdown, or designing empty and error states |
| `references/menu-bar.md` | the extension has or is gaining a `mode: "menu-bar"` command, or the menu bar shows stale data, a wrong count, or nothing |
| `references/store-publishing.md` | the user asks to publish, submit, or "make this store-ready", or `check-store-readiness.sh` reports a failure you want the reasoning behind |

## See also

- `react-best-practices` — hooks and component rules. A Raycast extension is
  React; this skill covers only what is Raycast-specific.
- `verifiable-surfaces` — `--help`/`--dry-run`/exit-code design, if you bundle
  helper scripts alongside the extension.
- `project-knowledge-harness` — the `pitfalls/` + `backlog/` + `TODO.md` format
  the gotchas below were originally recorded in.
- `pueue-job-queue` — the CLI the source extension wraps, as an example of what
  "the wrapped tool's own skill" means.

## Gotchas

- **`ray build` does not typecheck in its default `dev` environment.** esbuild
  strips types without checking them, so a genuine `TS2345` still prints
  `ready - built extension successfully` and exits 0. `ray build -e dist` *does*
  typecheck (it runs `tsc -p tsconfig.json --noEmit`) — but `npm run build`,
  `just build`, and `ray develop` are all the dev build, which is why the error
  survives long enough to ship. Keep `tsc --noEmit` in the gate.
- **`ray lint` does not typecheck either.** It runs ESLint, Prettier, and manifest
  and icon validation. It alone catches reserved-shortcut collisions, so you need
  both, and neither is sufficient.
- **Raycast runs extensions under launchd, which never sources a shell rc.** `PATH`
  is roughly `/usr/bin:/bin`, so a bare `pueue` / `gh` / `uv` fails with
  `spawn … ENOENT`. Probe **both** `/opt/homebrew/bin` (Apple Silicon) and
  `/usr/local/bin` (Intel) — hardcoding either breaks half of all Macs.
- **The dev terminal hides the PATH bug completely.** `ray develop`'s console
  inherits your interactive `PATH`, so a resolver that fails in production passes
  there. Exercise every environment-dependent change from Raycast root search.
- **Starting a daemon *from* Raycast poisons every job it later runs.** The child
  inherits Raycast's stripped environment. Only offer a one-click start when a
  service manager owns the daemon.
- **Never hand-write a `Preferences` or `Arguments` interface.** `ray build`
  generates `raycast-env.d.ts` with global `Preferences.<Command>` namespaces
  whose fields are *required*; consuming those turns manifest/code drift into a
  compile error. Commit it, and regenerate after every manifest edit.
- **Raycast has no multi-line preference type.** The allowed set is exactly
  `appPicker | checkbox | dropdown | password | textfield | file | directory`;
  `"type": "textarea"` fails `ray lint` with `must be equal to one of the allowed
  values`. `Form.TextArea` is a form component and unrelated. A "one per line"
  setting can only ever hold one entry — pick a single-line separator, and say why
  in the docs or a reader will assume it is arbitrary taste.
- **Manifest strings have schema minimums, and the error does not name the
  field.** `description` ≥ 16 chars, each `commands[].description` ≥ 12,
  `preferences[].description` ≥ 8, `tools[].description` ≥ 12. A one-word command
  description fails `ray lint` with a bare
  `must NOT have fewer than 12 characters` and a line number.
- **`useCachedPromise` persists to a disk-backed `Cache`.** Anything in the
  resolved value is written to disk in plaintext. One measured payload was 21x
  larger with its environment snapshot than without, and that snapshot contained
  every secret in the submitting shell.
- **Fetcher dependencies must be arguments to the hook, not closure captures.**
  The argument array is the cache key. A capture changes silently and keeps
  serving the previous account's data.
- **`mutate()` revalidates immediately by default, and many backends ack before
  they apply.** The follow-up read overwrites the optimistic update with
  truth-as-of-a-moment-ago and the action visibly undoes itself. Use
  `shouldRevalidateAfter: false` plus two delayed `revalidate()` calls — and
  measure your own backend rather than copying anyone's milliseconds.
- **An optimistic updater must return its input unchanged when the outcome is
  unknowable.** A wrong prediction flickers worse than no prediction.
- **Serving cached data on failure is right for flaky reads and wrong for
  structural ones.** A cached list renders a dead backend as live, with a toast
  that scrolls away as the only hint. Carry a `structural` flag and render a
  persistent banner.
- **Scope `useCachedState` and `LocalStorage` keys** by whatever the value belongs
  to — account, host, workspace. One shared key meant switching machines left a
  list filtered by a group that only existed elsewhere, showing nothing.
- **A `List.Dropdown` silently resets when its `value` is not among its children.**
  Seed a static fallback so the first paint is never empty, and render a synthetic
  `(gone)` item for an unknown current value.
- **Raycast has taken more shortcuts than `ray lint` knows about.** `⌘K` and
  `⌘P` are reserved (Open Action Panel, Open Search Bar Dropdown) and lint fails
  on them. But while an extension is *in development* Raycast also injects a
  Debug section into every action panel — `⌘R`, `⇧⌘S`, `⇧⌘D`, `⇧⌘X`, `⌘⌥D` —
  and **those pass lint, pass typecheck, and silently win over yours.** Your own
  action just does nothing, on your machine only. Full table, plus the real key
  combinations behind every `Keyboard.Shortcut.Common.*` (`Remove` is `⌃X`, not
  `⌘⌫`; `Duplicate` is `⌘D`, not `⌘⇧D`), in
  `references/ui-patterns.md` → *Shortcuts Raycast has already taken*.
- **`@raycast/api` bundles its own copy of `@types/react`.** Type props that hold
  JSX as `React.JSX.Element`, not `React.ReactNode` — the root `ReactNode` is a
  structurally different type that silently fails to match `ActionPanel`'s
  children.
- **`ray lint` runs MORE checks when it thinks it is in CI.** Locally it prints
  five steps; with `CI=true` it prints seven — `validate package-lock.json` and
  `validate other lock files` appear only there, and nothing announces the
  difference. Your local gate is a *subset* of the remote one, which is backwards.
  Put `CI=true` in the lint recipe so they cannot diverge. The check that bites:
  every `resolved` URL in the lockfile must be `registry.npmjs.org`, so a global
  npm registry pointing at a mirror (`registry.npmmirror.com`, an internal
  Artifactory) writes a lockfile the store rejects. Fix by host substitution —
  the tarballs are identical, so `integrity` stays valid, and
  `npm install --package-lock-only --registry=…` will *not* rewrite URLs already
  in the file.
- **`@types/node` must match Raycast's runtime (Node 22), not your shell's node.**
  Typing against 24 lets you write APIs that compile and throw at runtime.
- **Never `shell: true`.** Use `execFile` with an argv array. And raise
  `maxBuffer` — the 1 MB default truncates a large JSON read into a confusing
  parse error instead of a clear failure.
- **Global flags of clap-based CLIs must precede the subcommand.** `foo status
  --color never` exits **2** with a clap error; `foo --color never status` is
  correct. Exit codes are 0/1/2, not 0/1. Assert your generated argv against the
  CLI's own `--help` at verify time so a misplaced flag fails in the gate.
- **There is no test runner in a Raycast extension, and adding one is store-review
  noise.** Keep pure modules free of `@raycast/api` imports and assert them from
  one `dev-check.ts`, compiled by the already-installed `tsc` and run under
  `node`. That import discipline is the entire precondition — a barrel that pulls
  in the transport pulls in `@raycast/api` with it.
- **A `mode: "no-view"` command exports `async function Command(props)`, not a
  React component.** There is no window, so feedback is `showHUD`.
- **`ray lint` passes with a completely empty `metadata/`.** Screenshots, icon
  dimensions, the CHANGELOG placeholder, and a registered `author` are review-time
  requirements it never checks.
- **The extension IS the repo root, and `ray publish` copies the directory
  verbatim** — not the git index. Ignored-but-present directories ride along into
  the review PR. Export an allowlisted subset if that matters.
- **Fence untrusted program output in markdown**, and neutralise embedded
  backticks with a zero-width space. A build log full of `#` otherwise renders as
  headings, and a fence inside the output closes yours early.
- **`launchCommand` + `launchContext` is the supported cross-command path**, but
  push a view only once (guard with a ref — the receiving command re-renders on
  every poll) and wait for dependent async state before acting on the context.
  Acting early opens the previous account's data and looks like it worked.
- **A deeplink triggered from outside Raycast prompts for confirmation.**
  `launchCommand` from inside does not. So an external nudge is a poor substitute,
  and some verification genuinely needs a keypress rather than a URL.
