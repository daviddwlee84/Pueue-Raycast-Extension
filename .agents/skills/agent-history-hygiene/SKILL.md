---
name: agent-history-hygiene
description: Commit SpecStory transcripts and Claude/Cursor/OpenCode/Codex plans with feature diffs, derive staged `AI-Assisted-By` plus transcript/plan trailers, prevent secret leaks, and ignore SpecStory machine-local state. Use when asked to commit/save/stage agent sessions, record cross-harness provenance, bootstrap pre-commit, fix `.specstory/statistics.json` churn, scrub a transcript, or remediate accidental secret commits/pushes with rotate-first discipline.
---

# agent-history-hygiene

Keep agent transcripts and plan files in the commit that records their feature,
without leaking secrets or mutating a file while its recorder is active.

Surfaces, separated by purpose:

| Surface | Question it answers |
|---|---|
| `run-specstory-session.sh` | "How do I prove the recorder exited before finalization?" |
| `queue-agent-commit.sh` | "How do I queue this exact authorized commit without touching live history?" |
| `finalize-agent-commit.sh` | "How is the exact post-session snapshot sanitized and committed?" |
| `find-session.sh` | "Which transcript / plan file is my current session?" |
| `stage-agent-artifacts.sh` | "Which exact agent files belong in the prepared index?" |
| `agent-commit-metadata.sh` | "Which provenance trailers belong in the commit?" |
| `bootstrap-project.sh` | "How do I install validation-only hooks?" |
| `scan-staged.sh` | "Is the effective staged index clean?" |
| `probe-specstory-redaction.py` | "What does SpecStory redact natively?" |

## Core invariants

1. **Commit the transcript and plan with the feature they produced.** Never
   ignore `.specstory/history/` or silently drop dirty agent artifacts from the
   review trail.
2. **SpecStory state is not review history.** Ignore only
   `.specstory/.project.json` and `.specstory/statistics.json`; both are
   machine-local/regenerable.
3. **Quiescence comes before Git history movement.** An active recorder blocks
   commit, `pull --rebase`, rebase/update-base, integration, and worktree/session
   retirement. Required order:

   ```text
   recorder quiescence
     -> exact artifact + message finalization
     -> ordinary hook-enabled commit proven
     -> history/ref movement or retirement
   ```

4. **Provenance comes from the staged snapshot.** Use exact session, transcript,
   and plan selectors; never point a trailer at an unstaged artifact or guess an
   unknown model.
5. **Rotate at the provider before any Git rewrite.** Redaction prevents new
   reachability; it does not revoke a credential or erase every local copy.
6. **Never use plain `git push --force` as leak remediation.** It can destroy
   teammate work and does not remove bytes from existing clones/caches.
7. **Shipped files must be scanner-safe.** `npx skills add` installs this whole
   directory under a consumer's scan scope. Never embed a literal private-key
   header; build it at test runtime or use synthetic placeholders. Enforced by
   `tests/test_shipped_file_hygiene.py`.

## When to use

Use this skill when:

- the user asks to commit/save a SpecStory session, transcript, or plan;
- dirty `.specstory/history/*.md`, `.claude/plans/*.md`,
  `.cursor/plans/*.md`, or another configured artifact accompanies a feature;
- `.specstory/.project.json` or `statistics.json` keeps churning;
- the user asks to install pre-commit/gitleaks for agent artifacts;
- a scanner finds a secret in an agent artifact; or
- a credential was committed or pushed—read
  [`references/remediation.md`](references/remediation.md) before any rewrite.

Do not use it when the user explicitly wants agent history excluded, or when no
agent artifacts exist. If a leak is already on shared `main`/release, do not
suggest rewriting that branch; use the remediation runbook.

## Default lifecycle: queue now, finalize after exit

Read
[`references/post-session-finalization.md`](references/post-session-finalization.md)
before running or recovering this lifecycle. It defines every state, exit code,
recovery action, and no-retry boundary.

### 1. Launch the synchronous wrapper

From the intended worktree, before starting Claude:

```bash
bash skills/local/agent-history-hygiene/scripts/run-specstory-session.sh \
  --allow-commit claude
```

`--allow-commit` gives the **outer parent** a one-run finalizer capability. It
does not let the child infer user consent. The agent still waits for explicit
commit authorization before queueing.

Do not replace the wrapper with a Stop hook, detached watcher/daemon, or live
checkpoint. A Stop hook runs before process/recorder quiescence is proven; a
daemon separates recorder lifetime from the session; a live checkpoint commits
while the file can still change.

### 2. Work normally; stage only feature paths

Take the canonical lowercase UUID from Claude `/status`. Resolve the exact
SpecStory path and plan policy; if one UUID has multiple rendered aliases, pass
both UUID and path to disambiguate.

```bash
SESSION_ID=01234567-89ab-4cde-8fab-0123456789ab
TRANSCRIPT='.specstory/history/2026-09-01_02-14-50Z.md'
PLAN='.claude/plans/exact-change.md'

git add -- path/to/feature-a path/to/feature-b
```

Do **not** stage the live transcript or plan. The finalizer owns exact artifact
staging and sanitation after SpecStory exits.

### 3. After explicit user commit authorization, queue once

Create a UTF-8 base subject/body without lifecycle-managed trailers, then queue
with exact selectors:

```bash
MESSAGE_FILE="${TMPDIR:-/tmp}/agent-commit-message.txt"
printf '%s\n' \
  'feat(scope): summarize the change' \
  '' \
  'Explain why the change is needed.' > "$MESSAGE_FILE"

bash skills/local/agent-history-hygiene/scripts/queue-agent-commit.sh \
  --commit \
  --session-id "$SESSION_ID" \
  --specstory-path "$TRANSCRIPT" \
  --plan "$PLAN" \
  --message-file "$MESSAGE_FILE"
# Use --no-plan instead of --plan only when no plan exists.
```

The queue copies only the base message into private per-worktree state. It does
not read, stage, scan, redact, or commit the live transcript.

On `status=queued`, report **"finalization queued"** and exit the agent session.
Perform no further repository/index operation—not even `git status`, a diff, a
message edit, sync, or a finalizer call.

### 4. Let the outer runner finish

New runs use protocol v2 and default to **no cloud sync**; the explicit choice
reaches native run and sync flags without changing configuration. Existing v1
requests retain their strict original schema and recovery rules, never an
implicit upgrade. Only after child exit `0` and process-group quiescence, the runner:

1. records lifecycle proof;
2. runs one exact quiet UUID sync with the selected no-cloud policy;
3. verifies the selected alias's full digest against a bounded private native
   `sync --print --silent --no-cloud-sync --no-stats` export, binding the unchanged
   native JSONL generation before/after (unsupported exports or stale aliases stop);
4. calls the finalizer with its parent-held token;
5. stages and sanitizes exact artifacts in a locked alternate index;
6. derives canonical trailers, writes private handoff drafts, and calls one
   ordinary `git commit -F` with normal hooks enabled; and
7. proves the resulting parent, tree, branch, and
   `Agent-History-Request` trailer.

Normal pre-commit execution is unsafe while a writer is active: when unstaged
changes exist, pre-commit can stash them, check out/modify staged content, then
restore the stash. That checkout/restore sequence can race a recorder and
clobber, conflict with, or reintroduce bytes. Therefore the pre-commit checker is
validation-only; the quiescent finalizer is the sole sanctioned mutator.

## Finalization outcomes

- **Success (`committed` / exit 0):** an ordinary hook-enabled commit is proven.
  Only now may rebase/update-base, integration, or retirement proceed.
- **Sanitation (`rotation_required` / exit 10):** exact index and live artifacts
  are sanitized, private full beforeimages/occurrence evidence and drafts are
  retained, and no commit was attempted. A v2 read-only `--preview-review --json`
  shows masked findings; a private versioned `--review-file` accounts for every
  finding as `reviewed_noncredential`, `credential_rotation_required`, or
  `unresolved`. Complete reviewed fixtures release only this sanitized snapshot's
  rotation gate with fresh `--allow-commit`; real credentials still require
  rotation plus `--rotation-confirmed`, and unresolved/mixed incomplete review
  blocks. Nothing restores raw bytes, adds a policy allowlist, or skips hooks.
  Legacy v1 retains rotation-only recovery and cannot acquire v2 review evidence.
- **Partial publication (`publication_unproven` / exit 8):** private evidence is
  retained but index/source publication could not be fully proven. Inspect;
  never assume rollback or replay sanitation automatically.
- **Hook/commit failure (`commit_failed` / exit 11):** HEAD is unchanged and the
  exact prepared tree plus drafts remain. Fix the hook without changing that
  snapshot, then make one explicit recovery call with fresh `--allow-commit`.
  There is no automatic retry.
- **Uncertain outcome (exit 8):** reconcile only. Never issue another commit
  automatically; prove the exact HEAD or escalate for inspection.
- **Manual handoff:** LazyGit reads per-worktree
  `LAZYGIT_PENDING_COMMIT`; standard Git uses per-worktree `COMMIT_EDITMSG`.
  A draft makes recovery possible but is not proof that a commit happened.

An actual rebase is identified by Git's state directories (`rebase-merge` or
`rebase-apply`), not by a stale `REBASE_HEAD` file alone. Other active operation
markers and `index.lock` also block queue/finalization.

## Bootstrap a repository

Read
[`references/pre-commit-redaction-stack.md`](references/pre-commit-redaction-stack.md)
before bootstrap or migration.

> **Current hook release:** `ahh-v2.0.1` is the immutable published pin used by
> the checked-in template and hook migration target. Fresh bootstrap also pins
> gitleaks v8.30.1 and installs the scoped Sourcegraph/Git-OID false-positive
> fix. Updating only the ahh tag does not rewrite an existing root
> `.gitleaks.toml`; follow the rollout steps in the reference for existing repos.

```bash
bash skills/local/agent-history-hygiene/scripts/bootstrap-project.sh \
  --install-hook
pre-commit run --all-files
```

Bootstrap installs:

- a validation-only staged-index checker plus gitleaks;
- precise nested SpecStory state ignores, keeping `history/` visible;
- generic hygiene hooks whose mutators exclude every agent/archive/install root:
  `.agents`, `.claude`, `.codex`, `.cursor`, `.opencode`, `.specify`,
  `.specstory`; and
- optionally a validation-only `prepare-commit-msg` exact-selector gate.

Security checkers retain coverage of those roots. Excluding only `.specstory`
from whitespace/EOF/formatters is insufficient: `.agents/skills/` contains
installed vendored code, and `.claude/skills/` may symlink to it.

For old `ahh-v1.1.0` or exact local-redactor layouts, the post-release migration
is transactional and fail-closed:

```bash
bash skills/local/agent-history-hygiene/scripts/bootstrap-project.sh --migrate
```

It preserves compatible sibling hooks/options, validates a candidate before
publishing, and refuses ambiguous/customized legacy commands.

## Post-leak remediation

When a scan finds a real credential, or the user says it was committed/pushed:

1. stop any reflexive force-push;
2. read [`references/remediation.md`](references/remediation.md) end-to-end;
3. rotate at the provider regardless of blast radius; and
4. only then choose the least disruptive Git action from the runbook.

## Gotchas

- `.gitignore` does not untrack existing state. Use
  `bootstrap-project.sh --untrack-specstory-state`; never broaden the ignore to
  `.specstory/`.
- SpecStory >= 2.4.0 redacts on write but has context/rule gaps. Keep native
  redaction enabled and retain the finalizer check. Read
  [`references/specstory-native-redaction.md`](references/specstory-native-redaction.md)
  before changing placeholders or coverage.
- SpecStory 2.10 is checkout-path scoped, not branch scoped. Create the worktree
  first, then start one wrapper/session per change stream. `EnterWorktree` does
  not rebind an existing recorder.
- Exact discovery rejects incomplete JSONL, aliases, symlinks, unsafe bytes, and
  cross-worktree identity. Read
  [`references/transcript-session-discovery.md`](references/transcript-session-discovery.md)
  when resolution is empty or ambiguous.
- `plansDirectory` may be ignored at project scope in some Claude Code versions.
  Verify where the plan landed; user-level `"plansDirectory":
  "./.claude/plans"` is the recommended default.
- `pre-commit install` is per clone. CI is a second chance, not the last gate.
- `--install-hook` requires `core.hooksPath` genuinely unset. Empty, relative,
  global, or custom values are refused; integrate the validation-only gate in
  the configured hook directory yourself.
- `gitleaks protect` is deprecated. Use `gitleaks git --staged` or
  `gitleaks dir`.
- Rotate oversized sessions instead of raising the 2 MiB transcript allowance.

## Available scripts

- **`run-specstory-session.sh [--allow-commit] claude [-- OPTIONS...]`** — owns
  the foreground process group, preserves child/signal status, performs one
  post-exit exact sync, and optionally invokes one finalizer.
- **`queue-agent-commit.sh --commit --session-id UUID --specstory-path PATH
  (--plan PATH|--no-plan) --message-file PATH`** — writes one strict,
  metadata-only, per-worktree request; identical repeats are idempotent.
- **`finalize-agent-commit.sh --request ABSOLUTE_PATH
  (--allow-commit|--runner-token TOKEN) [--rotation-confirmed] [--review-file PATH]`**
  — validates lifecycle/snapshot proof, prepares exact sanitized artifacts and
  drafts, executes one ordinary commit, and reconciles uncertain state without
  retry. V2 `--preview-review --json` needs no authorization and never writes;
  `--prepare-only`, `--expected-revision DIGEST`, and `--reconcile-only` support a
  thin native adapter without a second commit engine.
- **`_post_session.py capabilities --json` / `inspect --request PATH --json`** —
  bounded side-effect-free protocol discovery and receipt reproof; `inspect
  --context --json` additionally requires authentic live wrapper ancestry.
  Public output contains identities/digests, never raw transcript/message values.
  Whole frozen-index text coverage is required in v2; product findings block
  without mutation, and unsupported binary/oversized coverage fails explicitly.
- **`find-session.sh (--session-id UUID|--specstory-path PATH|--newest)
  [--format specstory|claude|both] [--json]`** — exact checkout/session proof;
  `--newest` is heuristic compatibility only.
- **`stage-agent-artifacts.sh ...`** — exact or broad atomic staging.
  `--sanitize-index` sanitizes selected blobs in the locked alternate index;
  `--materialize-sanitized` safely writes those exact sanitized bytes back after
  generation checks; `--check-staged` never mutates.
- **`agent-commit-metadata.sh [--harness NAME --model NAME]
  [--format trailers|json]`** — derives canonical provenance from staged blobs.
- **`scan-staged.sh [--redact|--no-redact] [--config PATH] [--verbose]`** —
  validates the effective index; emits only bounded secret-free JSON findings
  and suppresses raw scanner output.
- **`probe-specstory-redaction.py [--json] [--keep] [--dry-run]`** — measures
  native coverage; exit 30 when SpecStory is absent.
- **`bootstrap-project.sh [--migrate] [--install-hook]
  [--untrack-specstory-state] [--force] [--dry-run]`** — installs/migrates the
  validation stack; remember the unpublished-v2.0.0 blocker above.

## Bundled assets

- `assets/artifact-dirs.txt` — canonical artifact directory set.
- `assets/pre-commit-config.yaml.template` — validation-only checker, gitleaks,
  and generic hygiene with full archive/install-root mutator exclusions.
- `assets/gitleaks.toml.template` — custom rules and scoped example allowlists.
- `assets/redact_secrets.py` — staged-index checker/sanitizer. `--check-index`
  accepts canonical or temporary commit indexes; `--fix-index` requires an
  explicit user-owned noncanonical `GIT_INDEX_FILE` and exact `--files`.

## Reference files

- [`references/post-session-finalization.md`](references/post-session-finalization.md)
  — load before queueing, finalizing, recovery, rebase, integration, or
  retirement.
- [`references/transcript-session-discovery.md`](references/transcript-session-discovery.md)
  — load when exact session/path resolution is empty, ambiguous, or mismatched.
- [`references/pre-commit-redaction-stack.md`](references/pre-commit-redaction-stack.md)
  — load when bootstrapping, migrating, tuning rules, or debugging scanner/hook
  behavior.
- [`references/specstory-native-redaction.md`](references/specstory-native-redaction.md)
  — load before changing native-redaction assumptions, sentinels, or coverage.
- [`references/remediation.md`](references/remediation.md) — load before any
  history rewrite or force-push after a leak.

## Tests

Run from the repository root:

```bash
make test-skill
```

The suite covers exact discovery/staging, alternate-index sanitation and
materialization, validation-only migration/hooks, scanner-safe output,
runner/finalizer authorization and recovery, stale `REBASE_HEAD`, private state,
and shipped-file safety. Read [`tests/README.md`](tests/README.md) for the full
inventory and direct commands.

## Related skills

- [`git-workflow`](../git-workflow/SKILL.md) — canonical Conventional Commit and
  provenance-message validation.
- [`project-knowledge-harness`](../project-knowledge-harness/SKILL.md) — project
  memory whose plan scratchpads this skill preserves in Git.
