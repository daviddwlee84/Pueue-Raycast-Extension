#!/usr/bin/env bash
# Run one foreground SpecStory/Claude session, then finalize only after exit 0.
# Bash 3.2 compatible (stock macOS).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
HELPER="$SCRIPT_DIR/_post_session.py"

usage() {
  cat <<'EOF'
Usage: run-specstory-session.sh [--allow-commit] claude [-- SPECSTORY_OPTIONS...]
       run-specstory-session.sh --provider claude [--allow-commit] [-- SPECSTORY_OPTIONS...]

Run `specstory run claude` synchronously on the foreground TTY. The child gets
only AGENT_HISTORY_REQUEST_PATH and AGENT_HISTORY_RUN_ID as lifecycle context.
An agent may queue one inert request; it cannot authorize or run the finalizer.

Required:
  claude                Explicit provider position (recommended).
  --provider claude     Equivalent flag form. Both protocols support Claude only.

Options:
  --allow-commit        Parent authorization for one automatic finalizer call,
                        only after the SpecStory child exits normally with 0,
                        its process group is empty, one exact quiet sync succeeds,
                        and a request exists.
  --no-cloud-sync       V2 default: carry no-cloud choice to native run and sync.
  --cloud-sync          V2 explicit per-run cloud choice; export stays no-cloud.
  --protocol-version N  New runs default to 2; explicit 1 retains legacy behavior
                        with no v2 review/cloud-choice authority. No upgrades.
  --                    Pass supported native options. Explicit --resume UUID is
                        pinned to the queued session; duplicate/nested -- or
                        config/output/redaction overrides fail closed in v2.
  --help, -h            Show this help and exit.

Noninteractive examples:
  run-specstory-session.sh claude -- --resume SESSION_UUID
  run-specstory-session.sh --allow-commit claude -- --no-cloud-sync

Without --allow-commit, a successful queued run is synced and retained, and
exits 0 with status=authorization_required. The run succeeded; only the commit
is outstanding. Read `status`, not just the exit code, before treating history
as committed. Finish explicitly with `finalize-agent-commit.sh --allow-commit`.
V2 requires the recorder's typed queue acknowledgement plus a full byte-identical
native read-only sync --print export and unchanged native source generation.
Unsupported export, stale selected aliases, and wrong-session evidence block;
never retarget an alias or write a marker into native history to force success.
For native co-commit adapters, launch WITHOUT --allow-commit so native guards
can run immediately before the externally authorized finalization.

Exit codes:
  0       child succeeded: no request was queued, finalization completed, or a
          queued request awaits explicit finalization (status=authorization_required)
  1-255   nonzero child status is preserved exactly
  2       invalid runner arguments
  3       required command missing
  10      sanitation changed content; rotate, then recover explicitly
  11      ordinary commit failed; prepared snapshot and drafts retained
  21      exact SpecStory sync failed; request retained
  22      child process group remained live; request retained, no sync/finalizer
  23      lifecycle proof could not be persisted
  128+N   child terminated by signal N; request retained, no finalizer
EOF
}

die() { printf 'error: %s\n' "$1" >&2; exit "${2:-2}"; }

PROVIDER=""
PROVIDER_SET=0
ALLOW_COMMIT=0
PROTOCOL_VERSION=2
CLOUD_SYNC=0
CLOUD_CHOICE_SET=0
SPECSTORY_ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --provider)
      [ "$PROVIDER_SET" = "0" ] || die "--provider may be passed only once"
      shift
      [ $# -gt 0 ] && [ -n "$1" ] || die "--provider requires claude"
      PROVIDER="$1"; PROVIDER_SET=1; shift ;;
    --provider=*)
      [ "$PROVIDER_SET" = "0" ] || die "--provider may be passed only once"
      PROVIDER="${1#--provider=}"; PROVIDER_SET=1
      [ -n "$PROVIDER" ] || die "--provider requires claude"
      shift ;;
    --protocol-version)
      shift; [ $# -gt 0 ] || die "--protocol-version requires 1 or 2"
      case "$1" in 1|2) PROTOCOL_VERSION="$1" ;; *) die "unsupported protocol" ;; esac
      shift ;;
    --cloud-sync)
      [ "$CLOUD_CHOICE_SET" = "0" ] || die "choose cloud policy only once"
      CLOUD_SYNC=1; CLOUD_CHOICE_SET=1; shift ;;
    --no-cloud-sync)
      [ "$CLOUD_CHOICE_SET" = "0" ] || die "choose cloud policy only once"
      CLOUD_SYNC=0; CLOUD_CHOICE_SET=1; shift ;;
    --allow-commit)
      [ "$ALLOW_COMMIT" = "0" ] || die "--allow-commit may be passed only once"
      ALLOW_COMMIT=1; shift ;;
    --help|-h) usage; exit 0 ;;
    --)
      shift
      SPECSTORY_ARGS=("$@")
      break ;;
    -*) die "unknown runner option (put SpecStory options after --; try --help)" ;;
    claude)
      [ "$PROVIDER_SET" = "0" ] || die "provider may be passed only once"
      PROVIDER=claude; PROVIDER_SET=1; shift ;;
    *) die "unsupported positional provider (expected claude; try --help)" ;;
  esac
done

if [ "$PROTOCOL_VERSION" = "1" ] && [ "$CLOUD_CHOICE_SET" = "1" ]; then
  die "versioned cloud choice requires protocol 2; v1 defaults are unchanged"
fi
[ "$PROVIDER_SET" = "1" ] || die "--provider claude is required"
[ "$PROVIDER" = "claude" ] || die "version 1 supports only --provider claude"
command -v python3 >/dev/null 2>&1 || die "python3 is required for safe lifecycle state" 3
[ -f "$HELPER" ] || die "private lifecycle helper is missing" 3

set -- python3 "$HELPER" run --script-dir "$SCRIPT_DIR" --provider "$PROVIDER" --protocol-version "$PROTOCOL_VERSION"
[ "$ALLOW_COMMIT" = "1" ] && set -- "$@" --allow-commit
[ "$CLOUD_SYNC" = "0" ] || set -- "$@" --cloud-sync
if [ "${#SPECSTORY_ARGS[@]}" -gt 0 ]; then
  exec "$@" -- "${SPECSTORY_ARGS[@]}"
fi
exec "$@"
