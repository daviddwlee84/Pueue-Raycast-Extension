#!/usr/bin/env bash
# Validate and finalize one post-session request after lifecycle quiescence.
# Bash 3.2 compatible (stock macOS).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
HELPER="$SCRIPT_DIR/_post_session.py"

usage() {
  cat <<'EOF'
Usage: finalize-agent-commit.sh --request ABSOLUTE_PATH --allow-commit [--rotation-confirmed]
       finalize-agent-commit.sh --request ABSOLUTE_PATH --runner-token TOKEN
       finalize-agent-commit.sh --request ABSOLUTE_PATH --preview-review --json
       finalize-agent-commit.sh --request ABSOLUTE_PATH --allow-commit --review-file PRIVATE_PATH

Validate one private post-session request against its exact worktree, journal,
HEAD/ref/index tree, session selectors, and successful outer-run lifecycle.
A request file alone is never commit authority.

Authorization (choose exactly one):
  --allow-commit        Explicit manual recovery authorization. Must be passed
                        again on every recovery attempt.
  --runner-token TOKEN  Parent-held one-run token supplied directly by
                        run-specstory-session.sh; not for child-agent use.
  --rotation-confirmed  Manual recovery acknowledgement after sanitation has
                        already returned status=rotation_required. Requires
                        --allow-commit and is never supplied by the runner.

V2 evidence/recovery options:
  --preview-review --json  Strictly readonly masked per-finding preview; no lock,
                          scanner execution, source/index, draft or journal write.
  --review-file PATH       Private versioned exact per-finding decisions; requires
                          fresh --allow-commit. Unknown/partial/stale review blocks.
  --prepare-only          Retain prepared proof/drafts without Git commit; requires
                          fresh --allow-commit. Does not release rotation gates.
  --expected-revision SHA Require this exact journal digest under finalizer lock.
  --reconcile-only        Prove/repair existing committing/done state only; never
                          stage or start a new Git commit.
  --json                  Explicit structured output (ordinary status is JSON too).

Required:
  --request PATH        Absolute .../agent-history-hygiene/runs/UUID/request.json.
  --help, -h            Show this help and exit.

The initial authorized pass stages and sanitizes exact artifacts, composes and
validates provenance, writes private drafts, then invokes one ordinary `git commit -F`.
If sanitation changes content it stops at the rotation gate. V2 requires complete
source-bound reviewed_noncredential / credential_rotation_required / unresolved
accounting, plus --rotation-confirmed when credential findings need rotation.
Reviewed fixtures release only this exact sanitized snapshot's gate; never restore
raw bytes, allow future scans, or skip hooks. V1 retains fresh
--allow-commit --rotation-confirmed recovery and cannot be upgraded silently.
Prepared recovery never re-stages; uncertain committing is reconciliation-only.
Partial source/index publication is reported as unproven, never as a rollback.

Output (stdout): one bounded JSON status object. Diagnostics go to stderr.
No transcript, diff, message, scanner output, or credential content is printed.

Exit codes:
  0   commit completed, or an already-completed commit was proven
  1   invalid arguments
  2   not inside a Git worktree
  3   required dependency unavailable
  4   malformed/unsafe request or journal
  5   authorization missing/invalid or wrong per-worktree request path
  6   stale Git state, active Git operation, lock, or missing lifecycle proof
  7   exact selector, staging, scanner, metadata, or message validation failed
  8   prior commit outcome cannot be proven; reconciliation only, never retry
  9   unrelated commit-message draft would be overwritten
  10  sanitation changed content; rotate credentials, then confirm recovery
  11  ordinary commit failed with HEAD unchanged; prepared snapshot retained
EOF
}

die() { printf 'error: %s\n' "$1" >&2; exit "${2:-1}"; }

REQUEST=""
REQUEST_SET=0
ALLOW_COMMIT=0
RUNNER_TOKEN=""
TOKEN_SET=0
ROTATION_CONFIRMED=0
PREVIEW_REVIEW=0
PREPARE_ONLY=0
EXPECTED_REVISION=""
RECONCILE_ONLY=0
REVIEW_FILE=""
JSON=0

while [ $# -gt 0 ]; do
  case "$1" in
    --request)
      [ "$REQUEST_SET" = "0" ] || die "--request may be passed only once"
      shift; [ $# -gt 0 ] && [ -n "$1" ] || die "--request requires an absolute path"
      REQUEST="$1"; REQUEST_SET=1; shift ;;
    --request=*)
      [ "$REQUEST_SET" = "0" ] || die "--request may be passed only once"
      REQUEST="${1#--request=}"; REQUEST_SET=1
      [ -n "$REQUEST" ] || die "--request requires an absolute path"
      shift ;;
    --allow-commit)
      [ "$ALLOW_COMMIT" = "0" ] || die "--allow-commit may be passed only once"
      ALLOW_COMMIT=1; shift ;;
    --runner-token)
      [ "$TOKEN_SET" = "0" ] || die "--runner-token may be passed only once"
      shift; [ $# -gt 0 ] && [ -n "$1" ] || die "--runner-token requires a value"
      RUNNER_TOKEN="$1"; TOKEN_SET=1; shift ;;
    --runner-token=*)
      [ "$TOKEN_SET" = "0" ] || die "--runner-token may be passed only once"
      RUNNER_TOKEN="${1#--runner-token=}"; TOKEN_SET=1
      [ -n "$RUNNER_TOKEN" ] || die "--runner-token requires a value"
      shift ;;
    --rotation-confirmed)
      [ "$ROTATION_CONFIRMED" = "0" ] || die "--rotation-confirmed may be passed only once"
      ROTATION_CONFIRMED=1; shift ;;
    --expected-revision)
      shift; [ $# -gt 0 ] && [ -n "$1" ] || die "--expected-revision requires a journal digest"
      [ -z "$EXPECTED_REVISION" ] || die "--expected-revision may be passed only once"
      EXPECTED_REVISION="$1"; shift ;;
    --reconcile-only) RECONCILE_ONLY=1; shift ;;
    --prepare-only) PREPARE_ONLY=1; shift ;;
    --preview-review) PREVIEW_REVIEW=1; shift ;;
    --json) JSON=1; shift ;;
    --review-file)
      shift; [ $# -gt 0 ] && [ -n "$1" ] || die "--review-file requires a private absolute path"
      [ -z "$REVIEW_FILE" ] || die "--review-file may be passed only once"
      REVIEW_FILE="$1"; shift ;;
    --help|-h) usage; exit 0 ;;
    -*) die "unknown option (try --help)" ;;
    *) die "unexpected positional argument (try --help)" ;;
  esac
done

[ "$REQUEST_SET" = "1" ] || die "--request is required"
if [ "$PREVIEW_REVIEW" = "1" ]; then
  [ "$ALLOW_COMMIT$TOKEN_SET$ROTATION_CONFIRMED$JSON" = "0001" ] && [ -z "$REVIEW_FILE" ] || \
    die "readonly preview requires --preview-review --json without authorization"
elif [ "$ALLOW_COMMIT" = "$TOKEN_SET" ]; then
  die "choose exactly one of --allow-commit and --runner-token TOKEN"
fi
[ -z "$REVIEW_FILE" ] || [ "$ALLOW_COMMIT" = "1" ] || die "--review-file requires fresh --allow-commit"
[ "$ROTATION_CONFIRMED" = "0" ] || [ "$ALLOW_COMMIT" = "1" ] || \
  die "--rotation-confirmed requires --allow-commit"
command -v python3 >/dev/null 2>&1 || die "python3 is required for strict lifecycle state" 3
[ -f "$HELPER" ] || die "private lifecycle helper is missing" 3

set -- python3 "$HELPER" finalize --script-dir "$SCRIPT_DIR" --request "$REQUEST"
if [ "$ALLOW_COMMIT" = "1" ]; then
  set -- "$@" --allow-commit
elif [ "$TOKEN_SET" = "1" ]; then
  # Keep a leading dash in a random parent token attached to its option; argparse
  # would otherwise parse it as another flag instead of token bytes.
  set -- "$@" "--runner-token=$RUNNER_TOKEN"
fi
[ "$ROTATION_CONFIRMED" = "0" ] || set -- "$@" --rotation-confirmed
[ "$PREVIEW_REVIEW" = "0" ] || set -- "$@" --preview-review
[ "$PREPARE_ONLY" = "0" ] || set -- "$@" --prepare-only
[ "$RECONCILE_ONLY" = "0" ] || set -- "$@" --reconcile-only
[ -z "$EXPECTED_REVISION" ] || set -- "$@" --expected-revision "$EXPECTED_REVISION"
[ "$JSON" = "0" ] || set -- "$@" --json
[ -z "$REVIEW_FILE" ] || set -- "$@" --review-file "$REVIEW_FILE"
exec "$@"
