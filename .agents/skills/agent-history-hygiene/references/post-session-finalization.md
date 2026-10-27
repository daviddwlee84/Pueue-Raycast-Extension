# Post-session finalization

Use this runbook for the default SpecStory/Claude lifecycle, any finalizer
recovery, and every rebase/integration/retirement decision that follows it.

## Protocol versions

New wrappers create **v2** requests/journals (POSIX, Python 3.11+). V1 has a separate strict parser;
existing requests are never upgraded by adding fields or reconstructing missing
beforeimages from today's files. `--protocol-version 1` explicitly creates a
legacy compatibility run; it is not a migration or a review bypass. V1 recovery
continues to require rotation confirmation after sanitation.

V2 records durable private sanitation evidence before publishing any sanitized
source/index: complete post-sync source beforeimages, original and sanitized
blob OIDs/modes, every finding and occurrence, replayable transformation steps,
native source/path identities, request/root/ref/parent/input/prepared tree,
scanner/tool/policy identities and complete coverage. Receipt-write failure stops
publication; a later partial publication is `publication_unproven`, not a claim
that all bytes rolled back. Private raw evidence is sensitive retention data,
not secure erasure, and never appears in public JSON or diagnostics.

## Hard boundary

An active transcript recorder blocks:

- `git commit` (including amend/fixup);
- `git pull --rebase`, `git rebase`, and update-base helpers;
- merge/cherry-pick/integration into another ref; and
- worktree, branch, or agent-session retirement.

Required order:

```text
recorder exits and the exact session is synced
  -> exact artifacts are staged/sanitized and the message is finalized
  -> one ordinary hook-enabled commit is proven
  -> only then may history/ref movement or retirement begin
```

A commit draft, a queued request, a clean scan, or a manual commit command is not
proof of completion by itself.

## Lifecycle diagram

```text
shell parent
  |
  | run-specstory-session.sh --allow-commit claude
  v
running: foreground `specstory run claude`
  |
  | child stages feature paths only
  | explicit user commit authorization
  | queue-agent-commit.sh + exact selectors
  v
pending: inert metadata-only request (no transcript read/mutation/commit)
  |
  | child reports "finalization queued" and exits; no more repo operations
  v
child_exited (must be exit 0)
  |
  | one exact UUID sync (v2 defaults no-cloud)
  | read-only native --print export; full digest + source-generation proof
  v
synced
  |
  | parent-held token authorizes one finalizer call
  | prove no writer + unchanged HEAD/ref/index tree
  | stage/sanitize exact artifacts in locked alternate index
  v
prepared ----------------------------+
  |                                    |
  | sanitation changed bytes           | message + provenance validation
  v                                    | handoff drafts
rotation_required                      v
  |                                  committing
  | exact per-finding review, or rotate |
  | fresh manual authorization          | one ordinary `git commit -F`
  | rotation confirmation when needed   v
  +----------------------------------> done (exact commit proven)

Any uncertain commit outcome remains `committing`: reconcile only, never retry.
```

No request means `done/no_request`. A nonzero/signaled child or failed exact sync
does not enter finalization.

## Authorization model

Three separate facts are required:

1. **Launcher capability:** `run-specstory-session.sh --allow-commit` lets the
   outer parent call the finalizer once after successful child exit and sync.
2. **User intent:** the child queues only after the user explicitly authorizes
   the commit. Launcher capability is not permission to infer that intent.
3. **Recovery authorization:** every manual finalizer invocation repeats
   `--allow-commit`. A stored request, journal, message, or draft is never commit
   authority.

`queue-agent-commit.sh --commit` names the requested action; it does not execute
or independently authorize a commit. `--runner-token` belongs only to the outer
runner and must never be passed to the child.

## Default commands

### 1. Start in the intended worktree

Create the worktree before the recorder. Then launch:

```bash
bash skills/local/agent-history-hygiene/scripts/run-specstory-session.sh \
  --allow-commit claude
```

Pass SpecStory options only after `--`, for example:

```bash
bash skills/local/agent-history-hygiene/scripts/run-specstory-session.sh \
  --allow-commit claude -- --resume SESSION_UUID
```

The child receives only `AGENT_HISTORY_REQUEST_PATH` and
`AGENT_HISTORY_RUN_ID`. Neither value authorizes finalization. V2 also proves the
live wrapper's native process identity and child ancestry before accepting a
queue; copying these environment strings into another process is insufficient.

### V2 no-cloud and exact export

No-cloud is the default for new runs. The wrapper passes native
`--no-cloud-sync --no-stats --no-usage-analytics --no-version-check` flags to run
and normal sync, without rewriting user/project configuration. `--cloud-sync`
is an explicit per-run choice, never an implicit configuration default. The
verification export always remains no-cloud, even for an explicitly cloud-enabled
normal sync. Conflicting cloud flags and config/output/redaction/timezone
redirects are unsupported rather than silently changing the reviewed source.

After successful child exit and process-group quiescence, the runner performs
one ordinary exact UUID sync, then reads a bounded private candidate from:

```text
specstory sync claude -s UUID --print --silent --no-cloud-sync --no-stats \
  --no-usage-analytics --no-version-check
```

It compares the **entire rendered digest** with the selected alias and binds the
native JSONL generation before sync, before export, and after export. An explicit
`--resume UUID` is pinned before launch and a queue for another UUID is refused.
For both resumed and new sessions, native JSONL must contain this run's exact
`queue_ack` in a typed tool result linked to a preceding tool call. User prose,
an older same-root transcript, and hand-written marker files are not evidence.
The child must let its real recorder retain the queue result and exit; never
write the acknowledgement into native history yourself. A UUID, mtime, or nonce
substring alone proves neither freshness nor content. Unsupported
flags/output, changing source, or a stale selected alias stops finalization;
other aliases are not removed or retargeted. Installed native compatibility is
verified only by the isolated synthetic native regression, never by a fake
provider success or a real user's session.

### 2. Prepare exact inputs inside Claude

Take the lowercase UUID from `/status`. Resolve an exact direct child of
`.specstory/history/`; if the UUID has aliases, supply the path as well. Decide
explicitly between one exact plan and no plan.

```bash
SESSION_ID=01234567-89ab-4cde-8fab-0123456789ab
TRANSCRIPT='.specstory/history/2026-09-01_02-14-50Z.md'
PLAN='.claude/plans/exact-change.md'

git add -- path/to/feature-a path/to/feature-b
```

Stage only intended non-artifact feature paths. V2 rejects any already-staged
artifact diff; it never silently removes one from the user's index. Do not stage
the live transcript or plan; do not use broad `git add -A`.

### 3. Queue only after explicit user commit authorization

The base message must be nonempty UTF-8 subject/body and must not already carry
`AI-Assisted-By`, `Agent-Transcript`, `Agent-Plan`, or
`Agent-History-Request`; the finalizer owns those trailers.

```bash
MESSAGE_FILE="${TMPDIR:-/tmp}/agent-commit-message.txt"
printf '%s\n' \
  'feat(scope): summarize the change' \
  '' \
  'Explain the reason for the change.' > "$MESSAGE_FILE"

bash skills/local/agent-history-hygiene/scripts/queue-agent-commit.sh \
  --commit \
  --session-id "$SESSION_ID" \
  --specstory-path "$TRANSCRIPT" \
  --plan "$PLAN" \
  --message-file "$MESSAGE_FILE"
```

Use `--no-plan` instead of `--plan` only when no plan exists. Both protocol
versions require a rendered SpecStory path.

A successful response is one bounded JSON object with `status=queued`. An
identical second request is idempotent; a different request conflicts and the
first remains authoritative.

After success:

1. say **"finalization queued"**;
2. exit the Claude session; and
3. perform no more repository/index operations—not status, diff, staging,
   message editing, sync, finalizer, rebase, integration, or retirement.

The queue captures the attached branch, HEAD object, and staged index tree. Any
later branch/index change makes it stale.

## Why finalization waits for process exit

### Normal pre-commit is unsafe during an active writer

When staged files also have unstaged changes, pre-commit can stash the unstaged
patch, check out or rewrite the staged view for hooks, and restore the patch
afterward. An active recorder can append between those checkout/restore steps.
The restore may conflict, clobber a generation, or reintroduce bytes that a hook
just removed. Even without a mutating hook, the recorder can change content
after validation but before the commit object is created.

Therefore:

- `check-agent-artifact-secrets` is validation-only;
- generic mutating hooks/formatters exclude all agent/archive/install roots;
- no hook sanitizes or re-stages a working-tree transcript; and
- the post-session finalizer is the sole sanctioned mutator.

### Why not a Stop hook, daemon, or live checkpoint

- A Claude Stop hook runs before child/process and recorder quiescence are
  proven. It cannot establish the post-exit sync boundary.
- A detached `specstory watch` daemon outlives the agent and breaks the parent's
  recorder-lifetime proof.
- A live checkpoint necessarily snapshots a file that can still be appended and
  invites repeated pre-commit/stash races.

The synchronous foreground wrapper is deliberate: the outer parent owns the
process group, forwards termination signals, waits for real exit, records proof,
then syncs and finalizes.

## What the finalizer proves and changes

On an initial authorized pass, the finalizer:

1. validates private run state, request digest, per-worktree path, successful
   child exit, exact completed sync, and parent authorization;
2. rejects active Git operations, locks, changed branch/HEAD/index tree, unsafe
   paths, and an active/changing transcript writer;
3. revalidates the exact UUID + SpecStory path and exact plan policy;
4. calls `stage-agent-artifacts.sh --session-only ... --sanitize-index
   --materialize-sanitized`;
5. derives provenance from staged blobs, composes one canonical message, and
   validates it;
6. writes owned per-worktree handoff drafts;
7. calls one ordinary `git commit -F` with `SKIP` removed and all hooks enabled;
   and
8. proves the new commit's branch, single parent, tree, and unique
   `Agent-History-Request` trailer.

Artifact staging runs under the real index lock against a copied alternate
index. `--sanitize-index` edits only exact selected stage-0 blobs, then requires
a clean post-check. `--materialize-sanitized` writes only those sanitized bytes
through Git checkout filters after proving the live clean hashes still match the
pre-sanitize blobs. The real index is atomically published only after success. V2 adds a no-replace,
fsynced `sanitation.json` receipt before source/index publication and a separate
immutable `publication.json` proof afterward. A failed later proof retains exact
beforeimages and any completed effects, reports `publication_unproven`, and
forbids automatic sanitation replay. V1 keeps its existing transaction contract.

## Redactor and scanner safety

V2 extends the existing checker, not a second scanner: every stage-0 text blob
in the complete frozen prepared index is scanned through the pinned gitleaks
policy, including unchanged product lines. Only the exact selected artifacts
may be sanitized; any product/unselected finding blocks before publication.
Coverage binds every path/mode/blob/digest to the prepared tree and is rechecked
on the frozen commit index. Gitlinks are explicitly non-blob entries. Unknown
modes, binary/NUL input, over 4,096 entries, files over 8 MiB, or aggregate inputs
over 128 MiB report unsupported coverage instead of a false clean result.

Scanner, Git, Python, Bash, helper, and policy identities are pinned before the first
scan and revalidated before receipt publication and commit. Mutable external
`[extend].path` scanner policies are unsupported. Nonidentity clean-filter
transformations of selected raw source bytes are also unsupported by v2's exact
replay proof; v1 retains its previous general clean/smudge behavior. No hook
installation/configuration changes occur, and a globally skipped hook is never
counted as scanner coverage.

`redact_secrets.py --check-index` may inspect the canonical index or Git's
commit-time temporary index. `--fix-index` refuses mutation unless
`GIT_INDEX_FILE` explicitly names an existing, user-owned, nonsymlink,
noncanonical regular file; it also requires exact unique `--files` under the
configured artifact roots.

Complete PEM/OpenVPN/PuTTY private-key records are replaced wholesale. A header
without a complete bounded record fails closed: partial redaction could leave
private material behind, so no index/worktree publication occurs.

Public wrapper/finalizer output is scanner-safe:

- raw gitleaks stdout/stderr is suppressed;
- `Secret` and `Match` never appear in `scan-staged.sh` JSONL, even with
  `--no-redact`;
- emitted fields are bounded/validated path, rule id, line, and commit marker;
- finalizer JSON reports status/ids only; and
- unsafe selectors and private hook/scanner diagnostics are not reflected.

`--redact` on `scan-staged.sh` masks scanner report output only; it never changes
files or an index.

### Local raw-object caveat

The exact staging transaction may cause Git to write the original live blob to
the local object database before the alternate index entry is replaced by its
sanitized blob. The raw object is then unreachable and is not included in the
prepared commit, but it can remain locally until Git garbage collection. This
is not secure erasure and does not cover shell history, recorder storage,
backups, or prior clones. Real credential exposure requires provider rotation;
sanitation is neither rotation nor proof that a synthetic fixture was a usable
credential. V2's complete per-finding review may release only an inapplicable
rotation gate for the exact already-sanitized snapshot. It does not remove the
private beforeimages or unreachable-object caveat. V1 still requires rotation
confirmation whenever sanitation changed bytes.

## States

| State | Meaning | Commit permitted? |
|---|---|---|
| `running` | foreground recorder/Claude child is active | No |
| `pending` | one inert exact request was queued | No |
| `child_exited` | child returned 0; sync not yet proven | No |
| `syncing` | one exact quiet sync is in progress | No |
| `synced` | exit 0 + exact sync/session proof recorded | Finalizer may prepare |
| `prepared` | exact sanitized tree and message proof retained | One freshly authorized finalizer recovery |
| `rotation_required` | sanitation changed bytes; commit was not attempted | V2 complete exact finding review + fresh authorization; credential decisions also require rotation confirmation. V1: rotation + two flags |
| `committing` | a prior ordinary commit outcome may be uncertain | Reconcile only; never retry |
| `done` | no request, or exact commit object proven | History movement allowed only for proven commit |
| `failed` | lifecycle stopped before a safe/proven completion | Follow status-specific recovery |

Private run directories are `0700`; request, journal, and owned messages are
`0600` under the current worktree's Git directory. A linked worktree cannot
consume another worktree's request.

## Outcome contracts

### Success

`status=committed`, exit 0 means:

- one ordinary commit returned success;
- all configured hooks ran normally;
- the exact parent/tree/ref and request trailer were proven; and
- the matching LazyGit pending draft was removed.

`status=already_committed`, exit 0 means a later reconciliation re-proved the
recorded commit without another commit attempt. Only these proven outcomes open
the rebase/integration/retirement gate.

### Rotation stop

`status=rotation_required`, exit 10 means:

- exact index and matching live artifacts were sanitized;
- prepared parent/tree and both drafts are ready;
- no commit or hook was attempted; and
- the gate remains unresolved until every finding is accounted for in v2 (or
  rotation is confirmed through the original v1 recovery contract).

### V2 read-only finding review

```bash
bash skills/local/agent-history-hygiene/scripts/finalize-agent-commit.sh \
  --request "$ABSOLUTE_REQUEST_PATH" --preview-review --json
```

Preview requires no commit authorization and writes no lock, journal, index,
source, draft, review file, or bytecode cache. It re-proves immutable receipt,
coverage, source generations, policy/tools, request, HEAD/ref, and prepared tree.
Public output contains only opaque finding IDs, safe paths/kinds, occurrence
counts, `[REDACTED]`, revisions, and private evidence/review locations. Inspect raw
values only in the private receipt; never paste them into logs or public reports.

Create a `0600` review file in a canonical `0700` private directory (normally the
reported `private_review_path`). Use canonical UTF-8 JSON: sorted keys, compact
separators, and a trailing newline. Do not commit it. Its versioned shape is:

```json
{
  "schema_version": 2,
  "request_revision": "<preview request digest>",
  "receipt_revision": "<preview receipt digest>",
  "prepared_tree": "<preview exact tree OID>",
  "decisions": [
    {
      "finding_id": "<one exact preview finding ID>",
      "disposition": "reviewed_noncredential",
      "reason": "Explain the source-bound evidence that this value is a synthetic fixture."
    }
  ]
}
```

The example is expanded for readability; serialize canonically when saving it.
Every finding must appear exactly once and its receipt must account for every
occurrence and every transformation. Dispositions are:

- `reviewed_noncredential`: source-bound evidence establishes a noncredential;
- `credential_rotation_required`: real/potential credential exposure remains;
- `unresolved`: unknown, mixed incomplete evidence, or no defensible decision.

An all-fixture review needs fresh `--allow-commit --review-file PATH`; it releases
only this exact sanitized tree's inapplicable rotation gate. Any credential
entry also requires actual provider rotation and fresh `--rotation-confirmed`.
Any unresolved/missing/duplicate/stale entry blocks, even if rotation is claimed.
No blanket allow, restoration of raw bytes, future scanner exception, hook skip,
or automatic uncertain-commit retry is available. Accepted review bytes remain
immutable and are re-proven even when inspecting a historical completed commit.

```bash
bash skills/local/agent-history-hygiene/scripts/finalize-agent-commit.sh \
  --request "$ABSOLUTE_REQUEST_PATH" --allow-commit --review-file "$PRIVATE_REVIEW_PATH"
# Add --rotation-confirmed only for reviewed credential findings after rotation.
```

### Legacy v1 rotation recovery

After rotation, resume exactly once with fresh manual authorization:

```bash
bash skills/local/agent-history-hygiene/scripts/finalize-agent-commit.sh \
  --request "$ABSOLUTE_REQUEST_PATH" \
  --allow-commit \
  --rotation-confirmed
```

The recovery validates the retained tree and clean index; it does not re-stage.
The runner never supplies `--rotation-confirmed`.

### Ordinary hook/commit failure

`status=commit_failed`, exit 11 means HEAD and the expected prepared tree are
unchanged. The prepared snapshot and drafts remain. Fix the hook/dependency
without altering that snapshot, then issue one fresh, explicit recovery:

```bash
bash skills/local/agent-history-hygiene/scripts/finalize-agent-commit.sh \
  --request "$ABSOLUTE_REQUEST_PATH" \
  --allow-commit
```

There is no automatic retry. If the index/HEAD changed, the status becomes
`commit_failed_snapshot_changed` or `stale_prepared_state`; retire that request
and queue a fresh run rather than retrying it.

### Manual handoff

The finalizer writes the complete composed message to both per-worktree Git
paths:

- LazyGit reads `LAZYGIT_PENDING_COMMIT`;
- standard Git uses `COMMIT_EDITMSG`.

Resolve them with `git rev-parse --git-path ...`; do not assume the primary
checkout's `.git/` path in a linked worktree. Foreign or user-edited drafts are
never overwritten.

Prefer authorized finalizer recovery. If the user deliberately commits from a
handoff draft, that is a manual escape hatch outside the managed proof path. The
draft is recoverable input, not proof; verify the actual commit parent/tree,
hooks, canonical trailers, and `Agent-History-Request` before allowing any
history movement. A `prepared` journal does not certify a manual commit.

## Thin native adapter protocol

The canonical helper remains the only queue/stage/sanitize/commit/reconcile
authority. A native caller may invoke the same Python subcommands through a
bound interpreter with `-I -B`; sibling code is loaded explicitly, not through
ambient `PYTHONPATH`.

```text
_post_session.py capabilities --json
_post_session.py inspect --context --json
_post_session.py inspect --request ABSOLUTE_PATH --json
_post_session.py queue --script-dir S --session-id UUID --specstory-path PATH \
  (--plan PATH|--no-plan) --message-file PATH
_post_session.py finalize --script-dir S --request PATH --allow-commit \
  --expected-revision JOURNAL_DIGEST [--prepare-only|--reconcile-only]
```

The public Bash queue's `--commit` is consumed by that wrapper; the direct
Python queue subcommand names the same intent without that flag. No native
caller may synthesize a journal, token, writer proof, receipt, or fallback commit.

`capabilities` returns schema/protocol version 2, helper content revision, and
finite capabilities including `inspect_v2`, `authentic_context_v2`,
`queue_idempotent_v2`, `sanitation_receipt_v2`, `review_v2`, `exact_export_v2`,
`no_cloud_v2`, `prepare_only_v2`, `revision_guard_v2`, `reconcile_only_v2`.
`inspect` returns request/journal digests, exact worktree/Git directory/ref/HEAD,
input index and prepared trees, provider/session/artifact selectors, base and
composed/normalized message digests (never bodies), commit OID, child/group/sync/
freshness/review status, receipt revision, authentic-context and commit-proof
booleans. `automatic_commit` is the launcher's frozen `--allow-commit` choice
(v1: null); `cloud_sync` similarly reports the explicit per-run cloud choice.
On macOS, helper subprocesses use the supported stock `/bin/bash`, not a
PATH-selected substitute. Historical done inspection re-proves the immutable original parent,
tree, whole normalized message and request trailer without requiring current
HEAD/index to remain at that old commit; native readiness checks reachability
separately.

Native co-commit integration starts the wrapper **without `--allow-commit`**, so
the parent cannot bypass a later native guard. New queue requires authentic
running context and `automatic_commit=false`. Queue succeeds before a native
binding CAS: failed native persistence must report `queued_but_not_bound`, retain
the exact request, and permit only idempotent inspect/bind repair. Do not hold a
native store lock across helper processes, scans, hooks, or commits.

To preserve actual session linkage when capturing helper stdout, the native
caller includes this exact secret-free object as its outer JSON `queue_ack`
field (and emits the same standalone JSON line in human mode):

```json
{"kind":"agent_history_queue_ack","schema_version":2,"request_id":"<run UUID>","request_path":"<absolute request>","request_revision":"<request digest>","helper_revision":"<helper digest>"}
```

The real recorder must capture this in a typed tool result; user prose and
manual native-history edits do not count. `queued_but_not_bound` also carries the
acknowledgement because the canonical queue really happened.

For a freshly authorized finalization, native may delegate `--prepare-only`,
inspect the exact retained receipt, recheck its own runtime/policy guard, then
call actual finalization with the new `--expected-revision`. The revision is
checked under the helper's existing finalizer lock before any effect.
`--prepare-only` never commits; rotation/unresolved findings remain a stop.
`--reconcile-only` can only re-prove/repair `committing` or `done`; another state
returns `reconciliation_required` without starting a commit. Never infer success
from exit zero, a draft, or a trailer alone.

## Exit codes

### Queue

| Exit | Contract |
|---:|---|
| 0 | request written, or identical request already exists |
| 1 | invalid/incomplete arguments |
| 2 | not in a Git worktree |
| 3 | dependency unavailable |
| 4 | selector/message validation failed |
| 5 | unsafe/stale state, no staged feature diff, or conflicting request |
| 6 | Git/lifecycle operation lock active |

### Runner

| Exit | Contract |
|---:|---|
| 0 | child succeeded; no automatic finalizer failed (may be no request or authorization-required) |
| 1–255 | nonzero child status preserved exactly |
| 2 | invalid runner arguments |
| 3 | dependency unavailable |
| 10 | finalizer stopped for rotation |
| 11 | ordinary commit failed; prepared snapshot/drafts retained |
| 21 | exact sync failed; no finalizer call |
| 22 | child process group remained live; request retained, no sync/finalizer |
| 23 | successful child exit could not be durably proven |
| 128+N | child terminated by signal N; request retained, no sync/finalizer |

### Finalizer

| Exit | Contract |
|---:|---|
| 0 | commit completed or already-completed commit re-proven |
| 1 | invalid arguments |
| 2 | not in a Git worktree |
| 3 | dependency unavailable |
| 4 | malformed/unsafe request or journal |
| 5 | authorization/path mismatch |
| 6 | stale Git state, active operation/lock, or missing lifecycle proof |
| 7 | exact selector, staging, scanner, metadata, or message validation failed |
| 8 | prior commit outcome unproven; reconciliation only, never retry |
| 9 | unrelated/edited draft would be overwritten |
| 10 | sanitation changed content; rotate and explicitly confirm recovery |
| 11 | ordinary commit failed; retry only when exact prepared snapshot is retained |

## Recovery and no-retry table

Every manual attempt requires fresh `--allow-commit`. Never loop on a finalizer
exit.

| Status/symptom | Safe next action | Forbidden action |
|---|---|---|
| `authorization_required` after runner exit 0 | run finalizer once with request path + `--allow-commit` | treating request as authority |
| child nonzero/signal | fix cause; start a fresh wrapper and requeue | syncing/finalizing the unproven run |
| `sync_failed` | fix sync; start a fresh wrapper/run so sync proof is journaled; requeue | hand-editing journal or calling commit directly |
| `active_writer` / transient exact validation failure | stop writer, preserve branch/index, retry once with fresh authorization | staging while writer remains active |
| `stale_state` before preparation | restage intended feature paths and queue a new run | forcing old request over changed tree/ref |
| `draft_conflict` | preserve or deliberately remove the foreign draft, then recover with fresh authorization | overwriting a nonmatching draft |
| `rotation_required` | rotate; use fresh `--allow-commit --rotation-confirmed` | committing before rotation; re-staging raw artifacts |
| `commit_failed` with prepared snapshot retained | fix hook only; fresh `--allow-commit` | automatic retry or changing the prepared tree |
| `commit_failed_snapshot_changed` | inspect and queue a new request | retrying old request |
| exit 8 / state `committing` | invoke only for reconciliation or inspect exact HEAD/journal | another `git commit` |
| manual commit from draft | independently verify exact commit and hooks | calling draft existence “proven” |

For state `committing`, a repeated finalizer invocation may only prove the exact
HEAD and return `already_committed`; it must never issue a second commit. If it
cannot prove the outcome, stop and inspect.

## Rebase and history boundary

Queue/finalization rejects merge, cherry-pick, revert, bisect, sequencer,
`rebase-merge`, `rebase-apply`, and index-lock state. This is both a consistency
gate and a history-safety gate: the queued request binds one attached branch,
parent, and index tree.

A stale `REBASE_HEAD` file alone is **not** an active rebase and must not block a
valid finalization. Detect real rebase state via the actual per-worktree Git
state directories:

```bash
git rev-parse --git-path rebase-merge
git rev-parse --git-path rebase-apply
```

Do not run update-base, `pull --rebase`, merge/integration, or retirement merely
because the finalizer wrote drafts or returned a recoverable status. Wait for a
proven `committed`/`already_committed` result, then begin a separate ordinary Git
operation.

## Cross-reference

- [`pre-commit-redaction-stack.md`](./pre-commit-redaction-stack.md) — staged
  checker, sanitation, scanner output, and unpublished release pin.
- [`transcript-session-discovery.md`](./transcript-session-discovery.md) — exact
  UUID/path proof and alternate-index staging.
- [`specstory-native-redaction.md`](./specstory-native-redaction.md) — native
  redaction coverage and residual gaps.
- [`remediation.md`](./remediation.md) — rotate-first response when a credential
  was already committed or pushed.
