# Feed the Raycast build back: a skill, the publish gate, and a launchd evaluation

## Context

Building `Pueue-Raycast-Extension` produced nine `pitfalls/` entries, four `backlog/`
notes, and a `docs/raycast-surfaces.md` that is already a mini-skill. None of that is
reusable today — it lives in one project. Meanwhile `agent-skills` has **zero** Raycast
or TypeScript-extension coverage (verified: `grep -ri raycast` over `skills/` returns
nothing).

Three things follow, one per question asked:

1. **Distil the experience into a skill.** The generic Raycast docs already cover
   `List`/`Form`. What they do not cover is what cost days here: `ray build` never
   typechecks, launchd strips `PATH` in a way the dev terminal hides, `useCachedPromise`
   writes to disk, and `ray lint` exits 0 with an empty `metadata/`.
2. **Close what can be closed on publishing.** Full automation is impossible — three
   blockers are human-in-the-loop by construction (below). Everything else can be a
   `just` recipe, and two of them (`ray build -e dist`, a clean-subset export) have
   never been run at all.
3. **Record the launchd evaluation** as a `backlog/` note in this repo, plus a docs page
   collecting store extensions that solve the same shape of problem.

Verified facts this plan rests on (all checked, not assumed):

| Claim | Evidence |
| --- | --- |
| `ray lint` passes with an empty `metadata/` | ran it — exit 0, all five stages green |
| `ray login` is browser OAuth; machine is not logged in | `ray profile` → "please first log in first using `npx ray login`" |
| `ray publish` has no `--dry-run`/`--yes`; `-I` only suppresses output | `ray publish --help` |
| `ray build` defaults to `-e dev`; `-e dist` is never run here | `ray build --help`, `package.json` scripts |
| `plutil` parses LaunchAgent plists but **not** `launchctl list <label>` output | ran both |
| `launchctl submit` still exists on Darwin 25.3 | `launchctl help`, `man launchctl` |

---

## Part A — the `raycast-extension-dev` skill

**Location:** `/Users/david/Documents/Program/agent-skills/skills/local/raycast-extension-dev/`

### A1. Scaffold

```bash
cd /Users/david/Documents/Program/agent-skills
bash skills/local/skill-author/scripts/new-skill.sh --local --no-symlinks raycast-extension-dev
```

`--no-symlinks` is deliberate: per `agent-skills/CLAUDE.md`, discovery symlinks are only
for skills useful *while working in that repo*. Nobody authors Raycast extensions there.

### A2. Frontmatter

Single-quoted (house rule — cheap insurance), and written with **no apostrophes** so no
`''` escaping is needed:

```yaml
---
name: raycast-extension-dev
description: 'Build, verify, and ship Raycast extensions in TypeScript with @raycast/api. Use when the user says "Raycast extension", "ray build", "ray develop", "ray lint", "menu bar command", "MenuBarExtra", "useCachedPromise", "raycast-env.d.ts", "no-view command", or wants to publish to the Raycast Store. Covers the launchd PATH trap, the typecheck ray build skips, optimistic-update reconcile timing, and the store-readiness checks ray lint never runs.'
---
```

Confirm the length lands in the 120–500 green tier with `lint-skill.sh` before committing.

### A3. `SKILL.md` body — ~415 lines, hard ceiling 500

Mirror `skills/local/pueue-job-queue/SKILL.md` section-for-section.

| Section | ~Lines | Content |
| --- | ---: | --- |
| Intro | 12 | Manifest-driven, esbuild-bundled, runs under launchd on Raycast's Node 22. The skill is the ~20% of facts that cost days. |
| `## When to use` / `## When NOT to use` | 23 | NOT: Script Commands (separate repo, no manifest); generic React → `react-best-practices`; the wrapped CLI's own semantics → that CLI's skill; AI `tools[]` without Pro; **taking** screenshots (it can verify them, not capture them). |
| `## The gate` | 26 | `tsc --noEmit && just verify && ray lint && ray build`, with a table of what each catches *and misses*. |
| `## Surface map` | 24 | Entry-point table from `docs/raycast-surfaces.md`. There is no widget API; `menu-bar` is the only way to show something without opening Raycast. |
| `## Repo layout` | 24 | The extension IS the repo root, and why; `ray publish` copies the directory verbatim. |
| `## Workflow A — scaffold` | 24 | Script → `npm install` → `npm run build` (this is what *generates* `raycast-env.d.ts`) → open **from Raycast root search**, not the dev console. |
| `## Workflow B — wrap a CLI behind a transport seam` | 38 | Mutations as a **data union**, not argv strings. `execFile` + argv array, never `shell: true`; raised `maxBuffer`; error taxonomy as a discriminated `kind`; strip secrets at the parse boundary. |
| `## Workflow C — data, cache, mutations` | 40 | Hook deps as *arguments*; one `abortable` per read; `shouldRevalidateAfter: false` + two **measured** delayed revalidates; optimistic updater returns input when unknowable; scope cache keys. |
| `## Workflow D — failure states as data` | 26 | One `ErrorDescriptor` with a `structural` flag, N renderers, actions as data not JSX. |
| `## Workflow E — the menu bar` | 26 | The full constraint checklist. |
| `## Workflow F — ship to the store` | 26 | The 10-item checklist, then `check-store-readiness.sh`, then the honest statement that `ray lint` exits 0 with an empty `metadata/`. |
| `## Available scripts` / `## Bundled assets` / `## Reference files` / `## See also` | 56 | Per below. |
| `## Gotchas` | 66 | ~26 bullets, each **bold claim** + correction. Stays in the body — by the time a reference loads, the bug has happened. |

Highest-value gotchas (each traceable to a `pitfalls/` file):

- **`ray build` does not typecheck** (esbuild strips types) — and `ray lint` does not
  either, though it alone catches reserved-shortcut collisions.
- **launchd never sources a shell rc**; `PATH ≈ /usr/bin:/bin`. Probe **both** Homebrew
  prefixes — hardcoding either breaks half of all Macs.
- **The dev terminal hides the PATH bug completely.** Exercise from Raycast root search.
- **Never hand-write `Preferences`/`Arguments`** — consume the generated globals so
  manifest↔code drift becomes a compile error.
- **`useCachedPromise` persists to a disk-backed `Cache`** — strip secrets at parse time.
- **`mutate()` revalidates immediately, and many backends ack before they apply**
  (measured here: ack ~22 ms, applied ~280 ms). Measure yours; do not copy 400/1500.
- **Cached-on-failure is right for flaky reads, wrong for structural ones.**
- **Raycast has no multi-line preference type** — the allowed set is exactly seven.
- **`⌘K`/`⌘P` are reserved and silently ignored.**
- **`@raycast/api` bundles its own `@types/react`** → type JSX props as
  `React.JSX.Element`, not `React.ReactNode`.
- **`@types/node` must match Raycast's Node 22**, not your shell's node.
- **`ray lint` passes with an empty `metadata/`.**
- **Menu bar:** no badge API; `isLoading` is a contract; restored from a DB across
  restarts; no `confirmAlert`; identical siblings cross handlers; cap the item count;
  never return `null`; background refresh is off by default for store installs.

### A4. `references/` — six files, each cited with a load condition

| File | Read it **when** |
| --- | --- |
| `runtime-and-subprocess.md` | the extension shells out, resolves a path, or reports `spawn … ENOENT` / "works in my terminal but not from Raycast" |
| `manifest-and-commands.md` | adding/renaming a command, adding preferences or arguments, wiring `launchCommand`, or considering AI `tools[]` |
| `data-and-state.md` | wiring `useCachedPromise`/`mutate`, an action visibly reverts then re-applies, or deciding what to persist and under which key |
| `ui-patterns.md` | building a List/Detail/Form, adding a dropdown or shortcut, rendering program output as markdown, designing empty/error states |
| `menu-bar.md` | the extension has (or is gaining) a `mode: "menu-bar"` command, or the menu bar shows stale data / a wrong count / nothing |
| `store-publishing.md` | the user asks to publish or "make this store-ready", or `check-store-readiness.sh` fails and you want the reasoning |

`raycast-surfaces.md` and `pueue-json-contract.md` from this repo are the seed material
for the first and last of those — generalise, do not copy pueue specifics.

### A5. `scripts/` — two, and four deliberate refusals

**`new-raycast-extension.sh`** — twelve interdependent files where the interdependencies
are the point (`commands[].name` must equal `src/<name>.tsx`; `tsconfig.include` must
list `raycast-env.d.ts` or the generated globals vanish; the Justfile gate must contain
all four stages).
Flags: `--dir --name --title --author --command NAME:MODE[:TITLE] (repeatable)
--license --no-verify-harness --dry-run --force --json --help`.
Exit: `0` ok · `1` bad args · `2` target not empty · `3` assets missing · `4` self-check
failed. Does not run `npm install`; prints ordered next steps instead.

**`check-store-readiness.sh`** — justified by a *verified* gap. Checks, each with an id:
`metadata-count` (3–6 PNGs) · `metadata-dimensions` (exactly 2000×1250 via `sips`) ·
`icon-present`/`icon-512`/`icon-not-placeholder` (sha256 vs the bundled placeholder) ·
`changelog-initial-version` · `license-file`+`license-field` · `author-registered` ·
`categories-nonempty` · `platforms-macos-when-menu-bar` · `lockfile-committed` ·
`env-dts-present` · `no-handwritten-preferences` · `readme-present`.
Flags: `--json` (default) `--quiet` `--strict` `--help`. Exit: `0/1/2/3/4`. Every failure
names its fix.

Refused, with reasons to record so nobody re-adds them: a `check-gate.sh` wrapper (a
four-line Justfile recipe already shipped as an asset); a PATH-probe script (it would run
in *your* shell — the exact environment that hides the bug); a reconcile-measurement
script (backend-specific; five lines of prose in `data-and-state.md`); a manifest
validator (`ray lint` already does it against the published schema).

Pinning nuance to write down: `script-design.md` says pin `npx` invocations, but `ray`
ships *inside* the project's own `@raycast/api`, so `npx ray build` must resolve from the
lockfile. The one exception is `npx @raycast/api@latest publish`, which is what Raycast
documents.

### A6. `assets/` — nine

`Justfile.template` (the gate + the self-bootstrapping `([ -d node_modules ] ||
npm install) &&` idiom) · `tsconfig.json.template` (the non-obvious line is
`"include": ["src/**/*", "raycast-env.d.ts"]`) · `eslint.config.mjs.template` (flat
config for `@raycast/eslint-config` v2) · `package.json.template` ·
`dev-check.ts.template` (assert helpers + the argv×`--help` cross-check) ·
`transport.ts.template` (the data-union seam) · `error-descriptor.tsx.template` ·
`metadata-README.md.template` · `extension-icon.placeholder.png` (a real 512×512 so a
fresh scaffold passes `ray lint`; its sha256 is its own tripwire in the checker).

Not bundled: a pitfall template (`project-knowledge-harness` owns that format — cite it),
and anything under ten lines that a heredoc handles.

### A7. Registration in `agent-skills`

1. `skills/.claude-plugin/marketplace.json` — a **new** plugin group `raycast-extensions`
   (single-skill groups are established practice: `version-control`, `music-notation`,
   `deep-research`; none of the existing groups fit). Leave the `NN-` prefixes alone.
2. `docs/skills/raycast-extension-dev.md` (+ `.zh-TW.md`), shaped like
   `docs/skills/pueue-job-queue.md`, with a **Provenance** section linking this repo's
   public `pitfalls/`.
3. A row in `docs/skills/index.md`; a `nav:` line in `mkdocs.yml`; a bullet in `README.md`.
4. Then `make marketplace && make validate && make docs-build`.

---

## Part B — close the automatable half of publishing

**Not automatable, by construction** — say this plainly in the docs rather than pretending:
screenshots need a GUI hotkey plus a *ticked* "Save to Metadata" (the community
`Capture Raycast Metadata` extension is resolution-dependent and unreliable);
`ray login` is browser OAuth; `ray publish` opens a PR against `raycast/extensions` that
a human reviews over days.

Everything below is new work in **this** repo.

### B1. `Justfile` — four recipes

- **`dist`** — `npx ray build -e dist`. Store parity. Never been run here.
- **`preflight`** — invoke the vendored
  `.agents/skills/raycast-extension-dev/scripts/check-store-readiness.sh`; if absent,
  print the `npx skills add` line and exit non-zero. This keeps Part B unblocked by
  Part A's push.
- **`shots`** — `just fixtures`, print the five-shot checklist, then open each command
  deeplink (`raycast://extensions/da-wei_lee/pueue/<command>`) one at a time, pausing for
  a keypress. You still press the capture hotkey; the recipe removes the sequencing and
  the "did I seed the queue?" question. Two honest caveats to print: a deeplink shows the
  external-trigger confirmation first (dismiss before capturing), and the **menu bar shot
  cannot be deeplinked** — click the icon.
- **`store-export`** — allowlist-`rsync` into `.build/store/`: `package.json`,
  `package-lock.json`, `tsconfig.json`, `eslint.config.mjs`, `raycast-env.d.ts`, `src/`,
  `assets/`, `metadata/`, `README.md`, `CHANGELOG.md`, `LICENSE`. Then run `ray lint` and
  `ray build -e dist` *there* to prove the subset is self-sufficient. Allowlist, not
  denylist — `ray publish` copies the directory, not the git index, so ignored-but-present
  `site/`, `.venv/`, `.specstory/` would otherwise ride along.
  **Unverified:** whether `ray publish` runs from a non-git temp dir. Treat `store-export`
  as a verification copy first; if publish refuses, fall back to publishing from root and
  record that in `backlog/store-publishing.md`.

### B2. `.github/workflows/extension.yml`

On `macos-latest` (the toolchain is macOS-shaped), path-filtered to `src/**`,
`package*.json`, `tsconfig.json`, `eslint.config.mjs`, `assets/**`:
`npm ci` → `npx tsc --noEmit` → `just verify` → `npx ray lint` → `npx ray build -e dist`.
This is the missing gate — today `just ci` is manual and the only workflow is `docs.yml`.

### B3. Content fixes

- **`CHANGELOG.md`** — fold `## [Remote Daemons]` into `## [Initial Version]`. Two
  unreleased `{PR_MERGE_DATE}` sections on an extension that has never shipped is a
  reviewer nit; neither section has been released, so there is nothing to preserve.
- **`backlog/store-publishing.md`** — record the six verified facts from the Context
  table, note that the ride-along set has grown well past "small and harmless"
  (`docs/`, `site/`, `.venv/`, `.specstory/`, `.agents/`), and mark `store-export` as
  built rather than hypothetical.
- **`TODO.md`** — replace the `[S]` screenshots line with the `just shots` procedure;
  add an `[S]` for de-staleing the "not on the Raycast store yet" copy in
  `docs/getting-started.md` **and** `docs/getting-started.zh-TW.md` post-merge.
- **Vendor the skill** once Part A is pushed:
  `npx skills@latest add daviddwlee84/agent-skills/skills --skill raycast-extension-dev`
  → updates `.agents/skills/` and `skills-lock.json`.

---

## Part C — the launchd evaluation

### C1. `backlog/launchd-jobs-extension.md`

Follow the house backlog format (`**Status:** P? · **Effort:** [?] · not started`, then
what / why not now / what would have to be true). The evaluation to write up:

**The mapping.** The gist compares `systemd-run --user` / `pueue` / `nohup` / `tmux`.
Raycast is macOS-only, so the systemd column becomes launchd:

| systemd | launchd | State |
| --- | --- | --- |
| `.service` unit | `~/Library/LaunchAgents/*.plist` | file-based, `plutil -convert json` parses it cleanly |
| `.timer` | `StartInterval` / `StartCalendarInterval` | plist keys, no separate object |
| `.path` | `WatchPaths` / `QueueDirectories` | plist keys |
| `systemd-run --user` (transient) | `launchctl submit` | **exists but deprecated, and keeps the job alive on failure** — wrong semantics for one-shot |
| `journalctl -u` | nothing equivalent | jobs must set `StandardOutPath`; otherwise output is scattered in `log show` |
| `systemctl show -p Result` | `launchctl print` | **unstructured** — see below |

**The load-bearing feasibility finding.** `plutil -convert json` reads a LaunchAgent plist
fine, but `launchctl list <label>` emits an old-style dict that `plutil` **rejects**
(`Property List error: Unexpected character {`), and `launchctl print` emits a bespoke
indented format. So the data model is: parse the plists for *definition*, and use only
the three-column `launchctl list` TSV (PID, LastExitStatus, Label) for *live state*.
That is a real parser to own and keep honest — the pueue extension got `--json` for free.

**Where it sits against the existing store.** `stevensd2m/launch-agents` already ships
list / load / unload / remove at 1,214 installs. The unoccupied ground is the part the
gist actually cares about: **one-shot supervised jobs with retrievable output** — the
`systemd-run --user` role. Doing that well means writing the plist yourself
(`RunAtLoad` + `StandardOutPath` under a scratch dir + `launchctl bootstrap` +
`bootout` on completion), because `launchctl submit` has the wrong restart semantics.

**Honest verdict to record.** Lower value than the pueue extension for the same effort:
no `--json`, no queue semantics, no parallelism control, and the surface that most needs
a GUI (transient one-shot jobs) is the one launchd supports worst. The `[M]`-sized
version worth doing is narrower — a **menu bar + list view over user LaunchAgents** that
surfaces `LastExitStatus`, next fire time from `StartCalendarInterval`, and tails
`StandardOutPath` — i.e. the observability half, where a `plist`-only data model is
sufficient and the existing extension is weakest.

**What would have to be true:** a real recurring need for launchd job observability on
this machine, and a decision that the pueue extension's remaining `[S]` items
(screenshots, interactive pass, real remote verification) are done first.

### C2. `docs/prior-art.md` + `docs/prior-art.zh-TW.md`

The collection the user asked for — store extensions solving the same shape of problem,
each with what it is precedent *for*:

- **Homebrew** (`brew`) — the closest structural precedent: requires a Homebrew-installed
  binary and ships a `menu-bar` command at `interval: 1m`. The title-as-badge idiom came
  from here.
- **Colima** (`MiskaMyasa/colima`), **OrbStack** (`nicholasq/orbstack`),
  **Yabai** (`krzysztoff1/yabai`) — three more shipped extensions that require a
  pre-installed CLI; collectively they are the answer to the "avoid additional downloads"
  review question in `backlog/store-publishing.md`.
- **Launch Agents** (`stevensd2m/launch-agents`, 1,214 installs) — the launchd niche as it
  stands today; list/load/unload/remove only. Prior art for Part C.
- **Capture Raycast Metadata** (`koinzhang/capture-raycast-metadata`, 819 installs) —
  relevant to Part B, and a worked example of *why* screenshots are not automatable: it
  only produces correct dimensions when the display's actual resolution is exactly twice
  its UI scaling.

Wire it in: `mkdocs.yml` `nav:` under `Reference:`, a `nav_translations` entry
(`Prior art: 相關擴充`), and a line in the `llmstxt` `sections: Reference:` list.

---

## Verification

**Part A** — run in order, from `agent-skills`:

```bash
bash skills/local/skill-author/scripts/lint-skill.sh --strict skills/local/raycast-extension-dev
bash skills/local/skill-author/scripts/lint-frontmatter.sh --parser node skills/local/raycast-extension-dev
awk 'END{print NR}' skills/local/raycast-extension-dev/SKILL.md      # < 500
for s in skills/local/raycast-extension-dev/scripts/*.sh; do
  bash -n "$s" && bash "$s" --help >/dev/null && bash "$s" --nonsense; echo "$s → $?"  # expect 1
done
make marketplace && make validate && make docs-build
```

`--parser node` matters: it is the same `yaml` package `npx skills` uses, and a bad
`description` is dropped there *silently, with exit 0*.

Then the end-to-end trial (needs network for `npm install` — confirm before running):

```bash
D=/tmp/ray-trial-$$
bash .../new-raycast-extension.sh --dir "$D" --name trial --author test_user \
  --command tasks:view --command queue-menu:menu-bar --dry-run     # writes nothing
bash .../new-raycast-extension.sh --dir "$D" ... --json
cd "$D" && npm install && npx ray build
grep -q "namespace Preferences" raycast-env.d.ts && npx tsc --noEmit && npx ray lint
```

The step that proves the skill's central claim, and must not be skipped:

```bash
npx ray lint;                                    echo "expect 0 with metadata/ empty → $?"
bash .../check-store-readiness.sh "$D" --json;   echo "expect 4 → $?"
# add three correctly-sized PNGs, re-run: the failure list must shrink by exactly one id,
# still reporting icon-not-placeholder and author-registered. Otherwise it is vacuous.
```

**Part B** — from this repo: `just check` still passes; `just dist` succeeds (first ever
run); `just store-export && cd .build/store && npx ray lint && npx ray build -e dist`
proves the subset is self-sufficient; `just preflight` exits 4 today and names
"3–6 screenshots at 2000×1250" as the reason; the new workflow goes green on a push.

**Part C** — `uv run mkdocs build` clean, both `prior-art` pages render, and the zh-TW nav
label appears. Every store link in `prior-art.md` resolves.

---

## Out of scope

- Taking the screenshots, running `ray login`, or running `ray publish`. Those are yours.
- Actually building a launchd extension — Part C is the written evaluation you asked for.
- Backfilling zh-TW twins for existing `agent-skills` docs pages beyond the one new page.
