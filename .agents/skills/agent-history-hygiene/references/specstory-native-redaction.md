# SpecStory's native redaction (v2.4.0+) and what it leaves us

SpecStory redacts secrets on write, before this skill's post-session sanitizer
and validation-only commit hooks inspect `.specstory/history/`. This page
records what upstream covers, what it misses, and why the finalizer check is
still required.

Regenerate everything below with:

```bash
bash skills/local/agent-history-hygiene/scripts/probe-specstory-redaction.py
```

## Upstream history

| Ref | What |
|---|---|
| [PR #235](https://github.com/specstoryai/getspecstory/pull/235) | `feat(redaction): automatically redact secrets from saved markdown history` — community PR by [@warnes](https://github.com/warnes), merged 2026-07-20 |
| [PR #253](https://github.com/specstoryai/getspecstory/pull/253) | A `gofmt` CI-fix fork of #235; closed in favor of #235 |
| **v2.4.0** (2026-07-20) | Shipped, **on by default**, covering local markdown **and** cloud sync |
| [#274](https://github.com/specstoryai/getspecstory/issues/274) (open) | Adjacent leak vector — `specstory watch` exposes the cloud auth token in its process command line |

The merged implementation is not the one in the PR. The maintainer swapped the
PR's 11 inline regexes for the [Betterleaks](https://github.com/betterleaks/betterleaks)
library ("we can leverage that community that is thinking about secrets
redaction as their sole focus") and unified the code paths that needed
redaction.

## Knobs

```bash
specstory sync  --no-redact-secrets     # also on `run` and `watch`
```

```toml
# .specstory/cli/config.toml (project) or ~/.specstory/cli/config.toml (user)
[redaction]
# Redact secrets and API keys from saved markdown history and cloud-synced
# session data. (default: true)
# enabled = false # equivalent to --no-redact-secrets
```

**`enabled` is the only knob.** The PR's `extra_patterns` (custom regexes) did
not survive the Betterleaks rewrite — there are no `extra_patterns` or
`GetRedactionExtraPatterns` symbols in the 2.9.0 binary. Repo-specific rules
have no upstream equivalent and stay our layer's job.

The placeholder is `[REDACTED:<betterleaks-rule-id>]` — the binary carries the
format string `[REDACTED:%s]`. Our redactor writes the same shape, and
`.gitleaks.toml` allowlists it, so whichever layer runs first the other sees an
inert placeholder and leaves the file alone.

## Coverage, measured

Probed against **specstory 2.9.0** / gitleaks 8.30.0. Each class is tested in
two contexts with two distinct tokens: `assign` (`KEY=<token>`) and `prose`
(the token in a sentence, the way a tool transcript prints one).

**SpecStory 36 · ours only 15 · uncovered by both 3** (of 54 class/context pairs)

### The structural finding: prose is the blind spot

Several classes are caught only in assignment context, and only by
betterleaks' entropy-based `generic-api-key` rule — which keys off a
`NAME = value` shape. The same token in prose sails through:

| Class | `assign` | `prose` |
|---|---|---|
| `openai-project-key` | SpecStory (`generic-api-key`) | **ours** |
| `huggingface-token` | SpecStory (`generic-api-key`) | **ours** |
| `notion-integration-token` | SpecStory (`generic-api-key`) | **ours** |
| `wakatime-api-key` | SpecStory (`generic-api-key`) | **ours** |
| `stripe-webhook-secret` | SpecStory (`generic-api-key`) | **ours** |

A transcript prints secrets in prose constantly — `cat .env` output, an error
message quoting a token, an agent echoing what it just read. Our
prefix-anchored rules do not care about surrounding syntax, so they hold in
both contexts.

### Covered by SpecStory in both contexts

`anthropic-api-key`, `openai-legacy-key`, `github-pat`,
`github-fine-grained-pat`, `github-oauth-token`, `github-actions-token`,
`gitlab-pat`, `google-api-key`, `groq-api-key`, `slack-bot-token`,
`stripe-secret-key`, `supabase-service-pat`, `linear-api-key`,
`private-key-pem`, `dotenv-dump`.

Notable: `private-key-pem` → `[REDACTED:private-key]`, exactly the label our
redactor now writes for PEM blocks.

### Ours in both contexts

`cursor-api-key`, `tailscale-auth-key`, `discord-webhook-url`,
`zapier-webhook-url`, `make-webhook-url` — the custom rules in
`assets/gitleaks.toml.template`. Betterleaks does not ship rules for these.

### Covered by nobody

| Class | Why |
|---|---|
| `aws-access-key-id` (both contexts) | gitleaks 8.30.0 has no dedicated `AKIA…` rule any more, and betterleaks doesn't either. In assignment form a *generic* rule may catch it; bare in prose nothing does. An access key ID is only half a credential (the secret access key is the sensitive half, and that one is high-entropy enough to be caught), so this is a defensible upstream choice rather than a hole to patch. |
| `telegram-bot-token` (prose only) | Caught in assignment form, missed in prose. Add a custom rule if you paste bot tokens into transcripts. |

### Negative controls

`sk-proj-XXX...`, `sk-ant-api03-XXX...`, `your-api-key-here`, `example-key`
and `OPENAI_API_KEY=REDACTED` all survive both layers. Transcripts can still
discuss key shapes in prose.

## What this means for our finalizer and hooks

1. **Keep the residual check.** 15 of 54 class/context pairs are ours alone,
   including every webhook URL rule and every prose-context custom key.
2. **Do not mutate during recording or pre-commit.** The supported flow waits
   for recorder exit and exact sync, then sanitizes exact staged blobs inside a
   locked alternate index. Pre-commit only validates the effective index.
3. **Keep placeholders stable.** The sanitizer writes SpecStory's sentinel
   shape, so content already cleaned by Layer 0 remains byte-stable. There is no
   mutating-hook, re-`git add`, or `SKIP` loop.
4. **`--legacy` changes bytes, not detection.** It writes pre-2.4.0
   placeholders for repositories that intentionally retain that older style.
   Detection and scope are unchanged; SpecStory version alone is not a reason
   to select it.
5. **Below 2.4.0 the finalizer is the only redacting layer.** Expect sanitation
   to return `rotation_required` when it replaces a real credential. Rotate
   before confirmed recovery; local raw objects created before replacement may
   remain unreachable until Git garbage collection.

## Re-running the probe

`scripts/probe-specstory-redaction.py` synthesizes a Claude Code session under
`~/.claude/projects/<slug>/`, renders it twice through
`specstory sync claude --print` (with and without `--no-redact-secrets`), diffs
per class, then re-scans the redacted markdown with our gitleaks config to
compute the residual set. It cleans up after itself unless `--keep`.

The synthetic project path is resolved before deriving `<slug>`. On macOS,
`/var` resolves to `/private/var`; SpecStory looks up sessions from the physical
subprocess cwd, so using the unresolved temp path produces `1 session not
found`. See
[`pitfalls/specstory-sync-1-session-not-found-under-macos-var.md`](../../../../pitfalls/specstory-sync-1-session-not-found-under-macos-var.md).

Probe tokens are generated at runtime from a seeded PRNG, so this repo carries
key *shapes* but never a literal high-entropy token that would trip a scanner
on the probe's own source.

`tests/test_specstory_coverage.py` locks in the two facts we actually depend
on — that redaction is still on by default, and that the placeholder is still
`[REDACTED:<label>]` — and skips when `specstory` isn't installed.

## Cross-reference

- [`post-session-finalization.md`](./post-session-finalization.md) — the
  recorder-exit, rotation, and recovery boundary.
- [`pre-commit-redaction-stack.md`](./pre-commit-redaction-stack.md) — where
  native redaction sits in the layered defense.
- [`../assets/redact_secrets.py`](../assets/redact_secrets.py) — index/worktree
  modes and the `--legacy` flag.
