# Pre-commit redaction stack

Use this reference when bootstrapping/migrating a repository, changing redaction
rules, diagnosing hook/scanner behavior, or reviewing the unpublished hook pin.

## Architecture

```text
Layer 0: SpecStory native redaction (>= 2.4.0, enabled by default)
          recorder-owned write to .specstory/history/*.md + cloud sync
                              |
                              | recorder exits; outer runner performs exact sync
                              v
Post-session finalizer (sole sanctioned mutator)
  stage-agent-artifacts.sh --sanitize-index --materialize-sanitized
  redact_secrets.py --fix-index on a locked noncanonical alternate index
                              |
                              | exact sanitized index + verified live bytes
                              v
Ordinary `git commit -F` with hooks enabled
  Layer 1: check-agent-artifact-secrets / deprecated alias (validation only)
  Layer 2: gitleaks-system                              (validation only)
  Layer 3: other repository hooks                       (normal behavior)
                              |
                              v
Prove exact parent + tree + ref + Agent-History-Request trailer
```

`scan-staged.sh` is an optional validation-only wrapper around gitleaks. It can
inspect the canonical index or an inherited alternate index, but never edits
content.

## The active-writer boundary

Do not run commit/pre-commit against an active transcript writer. Pre-commit may
stash unstaged changes, check out or rewrite the staged view for hooks, then
restore the stash. A recorder can append between those steps, causing a restore
conflict, clobbering a generation, or reintroducing bytes. A writer can also
change the transcript after validation but before Git creates the commit.

This is why the old `git add -> mutating hook -> re-add -> retry` workflow and
its `SKIP=...` escape hatch are retired. The supported boundary is:

```text
foreground recorder exit 0 -> exact sync -> finalizer sanitation -> ordinary commit
```

The pre-commit checker is validation-only. The post-session finalizer is the
sole sanctioned mutator.

Do not replace the boundary with:

- a Claude Stop hook, which runs before child/recorder exit is proven;
- a detached watcher/daemon, whose lifetime is no longer owned by the session;
  or
- a live checkpoint, which snapshots content while the writer can append.

See [`post-session-finalization.md`](./post-session-finalization.md) for the
full lifecycle and recovery rules.

## Layer 0: SpecStory native redaction

SpecStory >= 2.4.0 uses Betterleaks before writing Markdown or syncing to cloud
and emits `[REDACTED:<rule-id>]`. Our finalizer uses the same sentinel, so
already-cleaned bytes remain stable.

Layer 0 is useful but insufficient:

- it covers `.specstory/history/`, not plans/rules in other agent roots;
- Betterleaks misses several repo-specific keys and prose-context tokens; and
- recorder-owned native redaction does not prove the final staged snapshot is
  clean.

Keep it enabled. Coverage and knobs are in
[`specstory-native-redaction.md`](./specstory-native-redaction.md).

## Finalizer sanitation: the only mutating layer

The finalizer invokes exact staging with:

```bash
bash scripts/stage-agent-artifacts.sh \
  --session-only \
  --session-id "$SESSION_ID" \
  --specstory-path "$TRANSCRIPT" \
  --plan "$PLAN" \
  --sanitize-index \
  --materialize-sanitized
```

Use `--no-plan` only when no plan exists. Callers normally do not run this
command directly; `finalize-agent-commit.sh` supplies the request's exact
selectors after recorder quiescence.

The transaction:

1. acquires the real per-worktree index lock;
2. copies the current index to an alternate index;
3. adds only exact validated artifacts once;
4. runs `redact_secrets.py --fix-index --files ...` inside that alternate index;
5. runs `--check-index` as a clean postcondition;
6. optionally materializes exact sanitized blobs only after live clean-hash
   generation checks; and
7. atomically publishes the alternate index.

Any failure leaves the real index unchanged. Materialization can partially
sanitize live paths only if checkout itself fails; the command reports that
explicitly, the real index remains unchanged, and a rerun is safe after
inspection.

### `--check-index` versus `--fix-index`

`redact_secrets.py --check-index` reads stage-0 regular artifact blobs from the
effective index. It accepts:

- the canonical index;
- Git's temporary commit indexes (`commit -a` / `--only`); and
- a caller-owned alternate index.

`--fix-index` is guarded more tightly. It requires `GIT_INDEX_FILE` to name an
explicit existing, user-owned, nonsymlink, noncanonical regular file and refuses
an alias of the real index. `--files` must be unique exact Markdown artifacts
inside configured roots. Findings outside that exact set abort sanitation.

The compatibility `--fix` mode edits worktree files only and never stages them.
It is for explicit remediation, not the default commit lifecycle.

### Fail-closed private-key handling

Complete bounded PEM, OpenVPN, and PuTTY private-key records are replaced
wholesale with `[REDACTED:private-key]`. An isolated header -- a token quoted in
prose or captured test output, with no encoded payload after it -- gets the same
sentinel, because removing it strands no key bytes and lets a re-run converge.

A header that *is* followed by credible payload is neither redacted nor
committed: replacing only the header could leave private material behind, so it
fails closed and blocks finalization until a human resolves it. "Credible" means
the next line is a long encoded run (>=16 characters), a shorter run closed by
explicit base64 `=` padding, or a structural record field. The sanitizer computes
all transformations before any write, so one truncated key blocks the whole batch.

One sentinel covers every finding, matching the label SpecStory emits, so
downstream diffs never have to learn a second token.

Bare prose such as “private key” is not a record and remains unchanged. Shipped
tests construct scanner header tokens at runtime so installing this skill does
not trip downstream `detect-private-key`.

### Unreachable local raw objects

`git add` can write the original raw artifact blob into the local Git object
database before the alternate index entry is replaced with a sanitized blob.
That raw blob is unreachable and is not committed/pushed by the prepared tree,
but it may remain locally until garbage collection. Sanitation is not secure
erasure and cannot clean recorder stores, terminal history, backups, or other
clones. If bytes changed, the finalizer returns `rotation_required`; rotate the
credential before any commit recovery.

## Layer 1: validation-only pre-commit checker

The hook manifest exposes:

- `check-agent-artifact-secrets` — the new validation-only id; and
- `redact-agent-secrets` — a deprecated compatibility alias with identical
  validation-only behavior.

Both run:

```text
assets/redact_secrets.py --check-index
```

They enumerate the effective staged index themselves (`pass_filenames: false`),
never write the index/worktree, and fail when gitleaks output is missing,
malformed, unsafe, or non-clean. The alias name does not imply mutation.

The optional `prepare-commit-msg` gate installed by
`bootstrap-project.sh --install-hook` is also validation-only. With explicit
`AGENT_HISTORY_*` selectors it requires exact artifact and feature diffs in the
current commit index, including Git's temporary `-a`/`--only` index. Without
identity it visibly no-ops. It never stages.

## Generic mutators and archive/install roots

Generic fixers and formatters must exclude every agent/archive/install root, not
only `.specstory/history`:

```text
.agents  .claude  .codex  .cursor  .opencode  .specify  .specstory
```

The template applies one component-anchored regex to `end-of-file-fixer` and
`trailing-whitespace`. Apply the same exclusion to external Markdown/Python
formatters (including tools that format fenced code) and other auto-fixers.
These paths contain archival records, plans/rules, and installed vendored skill
code; `.claude/skills/` may symlink into `.agents/skills/`, so excluding only
one install root is insufficient.

Do **not** apply this mutator exclusion to security validation. The staged
checker, gitleaks, and `detect-private-key` retain coverage of agent/install
roots.

## Layer 2: gitleaks-system

The template runs `gitleaks-system` after the staged artifact checker. It sees
the exact sanitized index prepared by the finalizer and blocks any residual
finding. `.gitleaks.toml` supplies custom rules and scoped allowlists.

Fresh bootstrap pins gitleaks v8.30.1. That minimum is semantic, not cosmetic:
v8.22.1 silently accepts but ignores the newer global `[[allowlists]]` array,
including `targetRules`. Existing repositories keep their current scanner block
during `--migrate`; update gitleaks explicitly before relying on these scoped
allowlists.

### Allowlist design

- **Shared sentinel:** `[REDACTED:<rule-id>]`, scoped to the match rather than
  the whole line. A live key beside a sentinel still fires.
- **Legacy/example sentinels:** older redacted or deliberately truncated shapes.
- **Shipped test corpus:** path-scoped gitleaks allowance because skill tests are
  installed into downstream repositories. `detect-private-key` has no allowlist,
  so literal headers are absent from shipped bytes instead.
- **Artifact examples:** both path and regex must match. A real key inside a
  transcript still fires.
- **Sourcegraph vs Git OIDs:** only `sourcegraph-access-token` findings whose
  Secret is exactly bare 40-hex are allowed, and only inside artifact roots.
  Upstream's `sgp_...` forms still fire there; bare 40-hex outside artifacts
  still fires. This prevents scanner output plus full commit OIDs in an
  archival transcript from producing hundreds of false positives.

Use inline `#gitleaks:allow` only for a reviewed false positive. Never widen an
artifact-root allowlist to make a real leak pass.

## `scan-staged.sh`: validation with safe output

```bash
bash skills/local/agent-history-hygiene/scripts/scan-staged.sh
```

The wrapper suppresses raw scanner stdout/stderr, validates a bounded JSON-list
report, and emits one secret-free JSON object per finding with only validated
`rule_id`, `file`, `line`, and `commit` fields. It never emits gitleaks `Secret`
or `Match`, even with `--no-redact`.

`--redact` is the default and changes only scanner report masking; it does not
redact files or indexes.

| Exit | Meaning |
|---:|---|
| 0 | clean; no stdout |
| 10 | findings; scanner masking enabled |
| 20 | findings; scanner masking explicitly disabled, wrapper output still secret-free |
| 30 | gitleaks missing |
| 40 | scanner execution/report invalid; fail closed |
| 1 | invalid arguments |
| 2 | outside a Git repository |

## Bootstrap and migration

`bootstrap-project.sh` installs the validation-only config, gitleaks config,
precise SpecStory state ignores, and clone-local pre-commit hook. It optionally
installs the exact validation-only `prepare-commit-msg` gate.

Generic state rules stay narrow:

```gitignore
/.project.json
/statistics.json
```

Use `--untrack-specstory-state` for already tracked files; never ignore
`.specstory/` or `.specstory/history/`.

`--migrate` recognizes only:

- the exact `ahh-v1.1.0` remote hook; or
- the exact old `repo: local` vendored-redactor layout.

It preserves compatible sibling hooks and options, adds the full archive-root
mutator exclusion, validates a same-directory candidate, publishes atomically,
and removes a proven legacy script only after validation. Ambiguous/custom
commands fail without writes; `--force` does not weaken that rule.

## Release status: `ahh-v2.0.1` is published

The checked-in template and migration target use the immutable published
`check-agent-artifact-secrets@ahh-v2.0.1` pin. v2.0.1 keeps the v2 lifecycle and
validation-only hook contract while narrowing one upstream false positive:
full Git commit OIDs quoted in agent artifacts no longer impersonate legacy
bare-hex Sourcegraph tokens.

Fresh bootstrap installs the complete corrected stack: ahh-v2.0.1, gitleaks
v8.30.1, and the targeted allowlist in `.gitleaks.toml`.

Updating only the ahh hook tag does **not** rewrite a consumer's root
`.gitleaks.toml`; `--migrate` also preserves existing scanner blocks by design.
Existing v2.0.0 (and v1 migration) consumers must deliberately do all three:

1. update the agent-skills hook pin to `ahh-v2.0.1`;
2. update the gitleaks hook pin to `v8.30.1` or newer; and
3. merge the `sourcegraph-access-token` targeted allowlist from
   `assets/gitleaks.toml.template` into their reviewed root config.

Do not overwrite a customized scanner config wholesale. Verify the merged
config with the three-way corpus test: artifact Git OID clean, the same OID
outside artifacts blocked, and `sgp_...` inside artifacts blocked.

Future updates must deliberately advance the `ahh-v*` pin. Do not use broad
`pre-commit autoupdate` in a multi-skill monorepo where an unrelated tag could
be selected.

Keep these scopes synchronized when artifact roots change:

- `assets/artifact-dirs.txt`;
- `redact_secrets.py` default paths; and
- `.pre-commit-hooks.yaml` `files:` expressions.

The generic mutator exclusion intentionally includes the broader `.agents`
install root as well.

## Cross-reference

- [`post-session-finalization.md`](./post-session-finalization.md) — default
  lifecycle, outcomes, recovery, no-retry, and history boundary.
- [`transcript-session-discovery.md`](./transcript-session-discovery.md) — exact
  artifact identity and alternate-index transaction.
- [`specstory-native-redaction.md`](./specstory-native-redaction.md) — measured
  Layer 0 coverage.
- [`remediation.md`](./remediation.md) — rotate-first runbook after a committed
  or pushed leak.
