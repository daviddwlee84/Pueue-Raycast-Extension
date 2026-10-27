# Tests for agent-history-hygiene

## Run

From the repository root:

```bash
make test-skill
```

Direct runs:

```bash
GIT_CONFIG_GLOBAL=/dev/null uv run --extra dev pytest skills/local/agent-history-hygiene/tests/ -q
GIT_CONFIG_GLOBAL=/dev/null /bin/bash skills/local/agent-history-hygiene/tests/test_scan_staged.sh
GIT_CONFIG_GLOBAL=/dev/null /bin/bash skills/local/agent-history-hygiene/tests/test_find_session.sh
GIT_CONFIG_GLOBAL=/dev/null /bin/bash skills/local/agent-history-hygiene/tests/test_stage_agent_artifacts.sh
GIT_CONFIG_GLOBAL=/dev/null /bin/bash skills/local/agent-history-hygiene/tests/test_agent_commit_metadata.sh
GIT_CONFIG_GLOBAL=/dev/null /bin/bash skills/local/agent-history-hygiene/tests/test_post_session_finalize.sh
```

`make test-skill` uses `SYSTEM_BASH ?= /bin/bash` and
`GIT_CONFIG_GLOBAL=/dev/null`, including the post-session lifecycle suite.
Shell suites print `BASH_VERSION` and fail on Darwin unless they are actually
running under Bash 3.x, so Homebrew Bash cannot make the compatibility claim
vacuous. The metadata regression additionally invokes its helper directly as
`PATH=/usr/bin:/bin /bin/bash` to exercise stock Bash 3.2 with a restricted
lookup path. The isolated Git config prevents host hooks from changing
throwaway commits.

## Inventory

### Post-session runner/finalizer

- `test_post_session_v2.py` exercises strict new-run v2 evidence, full beforeimages
  and every occurrence/transformation, complete frozen-index coverage, mixed
  per-finding decisions, hostile/partial/stale/replayed evidence, no blanket
  rotation bypass, durable receipt-write failure before publication, and retained
  partial publication without rollback/retry claims. It also covers read-only
  masked preview/inspection, historical full-commit/review reproof, helper/tool/
  policy identity, native revision/prepare/reconcile boundaries, and authentic
  wrapper context.
- V2 synthetic process tests prove explicit resume UUID binding and real typed
  native queued-request acknowledgement linkage, including the native adapter's
  outer `queue_ack`. Old-session selectors, user prose, and copied environment
  values are not accepted as lifecycle proof. Cloud choice reaches native run/
  sync while exact print export always remains no-cloud; stale aliases and
  changing native sources block without retargeting/removal.
- Its installed-native regression creates only synthetic JSONL under an isolated
  HOME/project, runs one no-cloud/no-stats sync then native `sync --print`, and
  requires full byte equality plus an unchanged file/source inventory. This
  native compatibility result is separate from fake-provider tests; absent
  SpecStory is an explicit skip, never a compatibility claim. No agent launch or
  provider network is used. An optional `DEV_COCOMMIT_TEST_BINARY=/absolute/dev`
  test connects the real native adapter to a copied canonical helper, records
  its genuine returned `queue_ack` only in synthetic JSONL, and proves external
  prepare/revalidate/finalize with all four ordinary hooks exactly once.
- `test_post_session_finalize.sh` now explicitly selects protocol 1, retaining all
  original strict-schema and recovery assertions independently of v2.

- `test_post_session_finalize.sh` exercises the public help/argument contracts,
  foreground runner process group, exact single sync, child/signal status
  preservation, inert queue boundary, strict per-worktree request/journal
  ownership, exact selectors, and stale branch/index rejection.
- It proves no sync, sanitation, draft, hook, or commit occurs before child exit
  0; the child receives only request path/run id and cannot obtain the parent
  finalizer token.
- Success runs one ordinary commit with pre-commit, prepare-commit-msg,
  commit-msg, and post-commit once in the original worktree, then proves exact
  feature/artifact paths and canonical provenance/request trailers.
- Recovery coverage includes missing parent authorization, cross-worktree and
  symlink refusal, foreign/edited draft preservation, hook failure with retained
  prepared tree, uncertain `committing` reconciliation without retry, active
  writer refusal, rotation stop, and fresh
  `--allow-commit --rotation-confirmed` recovery without restaging.
- The rebase regression proves a stale `REBASE_HEAD` file alone is accepted,
  while real operation markers (for example `MERGE_HEAD`; implementation also
  checks `rebase-merge` / `rebase-apply`) block finalization.
- Output assertions capture status JSON/stdout and stderr separately, then use
  exact values or scoped matches. Test labels, fixture inputs, and expected
  diagnostic text are never searched as subject output, preventing false
  positives in leakage and absence checks.
- Those assertions ensure private hook/scanner diagnostics, unsafe selectors,
  transcript data, and credential bytes do not leak through status JSON/stderr.

### Exact discovery and staging

- `test_find_session.sh` covers bounded SpecStory prologues, local-time markers,
  strict complete JSONL parsing, canonical worktree roots, aliases, same-checkout
  leakage, subdirectory launches, symlink/control/UTF-8 rejection, conditional
  dependencies, and worktree isolation.
- `test_stage_agent_artifacts.sh` covers explicit selector/plan policy, exact and
  broad status parsing, custom/trailing-slash artifact roots, ignored/outside and
  unmerged paths, feature-diff enforcement, real index-lock races, atomic
  publication, and canonical trailer scope.
- Index-sanitation coverage proves:
  - `--fix-index` rejects a missing/canonical/aliased/nonregular alternate index;
  - incompatible `--sanitize-index` / `--materialize-sanitized` combinations
    fail before mutation;
  - clean and changed blobs publish with the right 0/10 status;
  - candidates are added exactly once;
  - changed live generations abort before materialization/publication; and
  - clean/smudge filter semantics are honored when sanitized bytes are written
    back.
- Hook tests cover validation-only normal, `commit -a`, and `commit --only`
  temporary indexes without mutation, plus primary/linked `core.hooksPath`
  refusal.

### Redactor and scanner

- `test_redact_secrets.py` tests pure replacements, stable SpecStory-compatible
  sentinels, prefix anchoring, scanner failure as non-clean, and complete
  PEM/OpenVPN/PuTTY record removal.
- Its index suite separates staged blobs from worktree bytes; rejects symlink,
  gitlink, unmerged, lookalike, and noncanonical-index inputs; preserves mode and
  nonsecret bytes; honors inherited alternate indexes; avoids reapplying clean
  filters; and proves idempotent exact multi-file sanitation.
- Incomplete/truncated private-key records fail closed without changing the
  index/worktree. Complete record removal converges even when gitleaks also
  reports the record.
- Malformed post-scans and scanner errors fail closed with secret-safe output.
  Compatibility `--fix` changes only worktree files and never re-stages.
- `test_scan_staged.sh` locks the wrapper contract: 0 clean, 10 masked finding,
  20 explicitly unmasked scanner mode, 30 missing binary, 40 execution/report
  failure, 2 outside Git, and 1 invalid arguments. Both finding modes omit
  `Secret`/`Match`; raw scanner stdout/stderr is suppressed; malformed,
  non-list, unsafe-path, oversized, and empty reports are handled explicitly.
- `test_gitleaks_corpus.py` stages expanded fixtures in throwaway repos and
  proves real shapes fire, examples are scoped correctly, custom webhook/key
  rules survive inside artifact roots, and the gitleaks config parses. Its
  Sourcegraph three-way guard proves a bare Git OID is clean only inside agent
  artifacts, the same OID still fires outside, and `sgp_...` still fires inside.
- `test_specstory_coverage.py` locks native-redaction defaults, private-key
  handling, placeholder shape, ours-only residual classes, and negative
  controls. It skips when SpecStory is unavailable.

### Bootstrap and migration

- `test_bootstrap_project.py` verifies the fresh validation-only template and
  identical scope of the new hook plus deprecated alias.
- Archive/install-root exclusions are component-anchored for exactly `.agents`,
  `.claude`, `.codex`, `.cursor`, `.opencode`, `.specify`, and `.specstory`.
- Migration tests cover exact `ahh-v1.1.0` remote and old local-redactor layouts,
  sibling hook preservation, compatible option preservation, legacy script
  provenance/removal, atomic candidate validation, dry-run, byte-stable
  idempotency, and refusal of ambiguous/custom commands without writes.
- Existing SpecStory state-ignore tests prove only `.project.json` and
  `statistics.json` are ignored/untracked and `history/` remains visible.
- `test_agent_commit_metadata.sh` covers staged transcript/model parsing,
  deduplication, spaced paths, JSON output, overrides, and bootstrap's global
  `core.hooksPath` refusal. It invokes the helper directly through stock Bash
  with a restricted `PATH`, including transcript-only/no-plan trailer and JSON
  output. Classified plan roots are `.claude/plans`, `.cursor/plans`,
  `.opencode/plans`, `.specify`, and `.codex`; `.cursor/rules` is deliberately
  not an `Agent-Plan` source.

### Shipped-file safety

- `test_shipped_file_hygiene.py` scans every shipped file and compiled redactor
  bytecode for `detect-private-key` blacklist substrings.
- It proves header-only input fails closed without modification and complete
  synthetic records redact convergently.
- This test is scanner-independent and must run everywhere; installing the skill
  places `assets/`, `tests/`, fixtures, and references inside the consumer's
  repository scan scope.

## Test-vector hygiene

Firing fixtures use deliberately fake, realistic secret shapes plus
`<!-- gitleaks:allow -->` so a repo-root/downstream scan does not flag the skill
source itself. Tests strip the marker only inside throwaway repositories before
scanning.

Two scanner classes need runtime placeholders instead of a marker:

- GitHub's provider scanner does not honor gitleaks markers for Stripe webhook
  shapes, so source uses `__SYNTHETIC_STRIPE_WEBHOOK_SECRET__`.
- `detect-private-key` does plain substring matching and supports no allowlist.
  Fixtures therefore use `__SYNTHETIC_PEM_BEGIN__`,
  `__SYNTHETIC_PEM_END__`, and `__SYNTHETIC_PEM_HEADER_OPENSSH__`; tests expand
  headers through `pem_header()` / `pem_block()` at runtime.

`FIXTURE_PLACEHOLDERS` in `conftest.py` is the single source of truth. Python and
shell staging helpers expand placeholders only in temporary repos.

The marker strip must be byte-exact: length-sensitive OpenAI/Anthropic/webhook
rules stop matching if token length changes. Add a firing vector as:

```text
<SECRET> <!-- gitleaks:allow -->
```

The strip reclaims exactly the leading separator. `clean.md` and
`example_shapes.md` carry no marker and must remain non-firing. GitHub-native
secret scanning is configured separately because it ignores the marker.

## Requirements

- `uv` for the pytest command;
- `git` with a usable test identity (shell suites also pass local identity);
- `gitleaks` for real corpus/integration checks—those checks skip or xfail with
  a clear reason when absent; and
- SpecStory only for native coverage tests, which skip when unavailable.
