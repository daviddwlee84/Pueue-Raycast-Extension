#!/usr/bin/env bash
# Queue one inert post-session commit request. Never stages or reads transcript data.
# Bash 3.2 compatible (stock macOS).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
HELPER="$SCRIPT_DIR/_post_session.py"

usage() {
  cat <<'EOF'
Usage: queue-agent-commit.sh --commit --session-id UUID \
         --specstory-path PATH (--plan PATH | --no-plan) \
         --message-file PATH

Queue the current staged feature snapshot for post-session finalization. This
command must run inside the Claude process launched by run-specstory-session.sh.
It validates paths and Git metadata, copies only the base commit message, and
writes one private JSON request. It never reads, stages, scans, or redacts the
live transcript and never commits.

Required:
  --commit                 Explicitly request the commit action.
  --session-id UUID        Canonical lowercase Claude session UUID.
  --specstory-path PATH    Existing direct .specstory/history/*.md selector.
  --plan PATH              Existing exact Markdown plan under a configured
                           agent-artifact directory.
  --no-plan                Explicitly state that this session has no plan.
  --message-file PATH      UTF-8 base commit subject/body (relative to git root
                           unless absolute; copied into private run state).
  --help, -h               Show this help and exit.

Exactly one of --plan and --no-plan is required. Stage feature paths first.
After a successful queue, make no more repository/index changes: report
"finalization queued" and exit the agent session.

Output (stdout): one JSON object with status=queued and idempotent=true|false.
Diagnostics go to stderr and never include transcript, diff, or secret content.

Exit codes:
  0  request written, or an identical request already exists
  1  invalid arguments
  2  not inside a Git worktree
  3  required dependency unavailable
  4  selector/message validation failed
  5  unsafe/stale state, no staged feature diff, or conflicting request
  6  Git operation/lifecycle lock is active
EOF
}

die() { printf 'error: %s\n' "$1" >&2; exit "${2:-1}"; }

COMMIT=0
SESSION_ID=""
SESSION_SET=0
SPECSTORY_PATH=""
SPECSTORY_SET=0
PLAN=""
PLAN_SET=0
NO_PLAN=0
MESSAGE_FILE=""
MESSAGE_SET=0

while [ $# -gt 0 ]; do
  case "$1" in
    --commit)
      [ "$COMMIT" = "0" ] || die "--commit may be passed only once"
      COMMIT=1; shift ;;
    --session-id)
      [ "$SESSION_SET" = "0" ] || die "--session-id may be passed only once"
      shift; [ $# -gt 0 ] && [ -n "$1" ] || die "--session-id requires a UUID"
      SESSION_ID="$1"; SESSION_SET=1; shift ;;
    --session-id=*)
      [ "$SESSION_SET" = "0" ] || die "--session-id may be passed only once"
      SESSION_ID="${1#--session-id=}"; SESSION_SET=1
      [ -n "$SESSION_ID" ] || die "--session-id requires a UUID"
      shift ;;
    --specstory-path)
      [ "$SPECSTORY_SET" = "0" ] || die "--specstory-path may be passed only once"
      shift; [ $# -gt 0 ] && [ -n "$1" ] || die "--specstory-path requires a path"
      SPECSTORY_PATH="$1"; SPECSTORY_SET=1; shift ;;
    --specstory-path=*)
      [ "$SPECSTORY_SET" = "0" ] || die "--specstory-path may be passed only once"
      SPECSTORY_PATH="${1#--specstory-path=}"; SPECSTORY_SET=1
      [ -n "$SPECSTORY_PATH" ] || die "--specstory-path requires a path"
      shift ;;
    --plan)
      [ "$PLAN_SET" = "0" ] || die "--plan may be passed only once"
      shift; [ $# -gt 0 ] && [ -n "$1" ] || die "--plan requires a path"
      PLAN="$1"; PLAN_SET=1; shift ;;
    --plan=*)
      [ "$PLAN_SET" = "0" ] || die "--plan may be passed only once"
      PLAN="${1#--plan=}"; PLAN_SET=1
      [ -n "$PLAN" ] || die "--plan requires a path"
      shift ;;
    --no-plan)
      [ "$NO_PLAN" = "0" ] || die "--no-plan may be passed only once"
      NO_PLAN=1; shift ;;
    --message-file)
      [ "$MESSAGE_SET" = "0" ] || die "--message-file may be passed only once"
      shift; [ $# -gt 0 ] && [ -n "$1" ] || die "--message-file requires a path"
      MESSAGE_FILE="$1"; MESSAGE_SET=1; shift ;;
    --message-file=*)
      [ "$MESSAGE_SET" = "0" ] || die "--message-file may be passed only once"
      MESSAGE_FILE="${1#--message-file=}"; MESSAGE_SET=1
      [ -n "$MESSAGE_FILE" ] || die "--message-file requires a path"
      shift ;;
    --help|-h) usage; exit 0 ;;
    -*) die "unknown option (try --help)" ;;
    *) die "unexpected positional argument (try --help)" ;;
  esac
done

[ "$COMMIT" = "1" ] || die "--commit is required"
[ "$SESSION_SET" = "1" ] || die "--session-id is required"
[ "$SPECSTORY_SET" = "1" ] || die "--specstory-path is required"
[ "$MESSAGE_SET" = "1" ] || die "--message-file is required"
if [ "$PLAN_SET" = "$NO_PLAN" ]; then
  die "choose exactly one of --plan PATH and --no-plan"
fi
command -v python3 >/dev/null 2>&1 || die "python3 is required for strict JSON state" 3
[ -f "$HELPER" ] || die "private lifecycle helper is missing" 3

set -- python3 "$HELPER" queue --script-dir "$SCRIPT_DIR" \
  --session-id "$SESSION_ID" --specstory-path "$SPECSTORY_PATH" \
  --message-file "$MESSAGE_FILE"
if [ "$PLAN_SET" = "1" ]; then
  set -- "$@" --plan "$PLAN"
else
  set -- "$@" --no-plan
fi
exec "$@"
