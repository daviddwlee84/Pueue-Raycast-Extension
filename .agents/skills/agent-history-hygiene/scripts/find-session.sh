#!/usr/bin/env bash
# find-session.sh — resolve an exact SpecStory / Claude Code session for a git checkout.
#
# Bash 3.2 compatible (stock macOS).

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: find-session.sh (--session-id UUID | --specstory-path PATH | --newest) [OPTIONS]

Resolve agent session files for the current git checkout. Exact selectors are
required unless the caller explicitly opts into the compatibility heuristic.
Relative paths are resolved from the git root, not the invocation directory.

Selectors:
  --session-id UUID       Select an exact canonical lowercase Claude Code UUID.
                          When SpecStory output is requested, its header must match.
  --specstory-path PATH   Select one exact direct-child
                          .specstory/history/*.md file and derive/verify its UUID.
  --newest                Explicitly use independent newest-file heuristics.
                          Cannot be combined with exact selectors.

Options:
  --format VALUE          specstory, claude, or both (default: both).
  --json                  Emit one JSON object instead of TSV.
  --quiet                 Suppress stderr diagnostics; structured stdout remains.
  --help, -h              Show this help and exit.

TSV output keys:
  status                  resolved | not_found | ambiguous | mismatch | dependency_error | error
  confidence              exact | heuristic | none
  source                  session-id | specstory-path | session-id+specstory-path | newest
  specstory_path          absolute path or empty
  claude_session_uuid     canonical lowercase UUID or empty
  claude_jsonl_path       absolute path or empty
  candidate               repeated absolute candidate path (only when relevant)

Exit codes:
  0  requested artifacts resolved
  1  invalid arguments
  2  not inside a git repository
  3  requested artifact not found
  4  exact selector is ambiguous; candidates are reported
  5  selected path/header/JSONL identity failed validation
  6  required runtime dependency missing (`iconv`, or `python3` for Claude JSONL)
EOF
}

QUIET=0
log()  { [ "$QUIET" = "1" ] || printf '%s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$1" >&2; exit "${2:-1}"; }

FORMAT="both"
OUT_JSON=0
SESSION_ID=""
SESSION_ID_SET=0
SPECSTORY_INPUT=""
SPECSTORY_PATH_SET=0
USE_NEWEST=0

while [ $# -gt 0 ]; do
  case "$1" in
    --session-id)
      shift
      [ $# -gt 0 ] && [ -n "$1" ] || die "--session-id needs a canonical lowercase UUID (value not shown)" 1
      SESSION_ID_SET=1; SESSION_ID="$1"; shift ;;
    --session-id=*)
      SESSION_ID_SET=1; SESSION_ID="${1#--session-id=}"
      [ -n "$SESSION_ID" ] || die "--session-id cannot be empty" 1
      shift ;;
    --specstory-path)
      shift
      [ $# -gt 0 ] && [ -n "$1" ] || die "--specstory-path needs a non-empty path (value not shown)" 1
      SPECSTORY_PATH_SET=1; SPECSTORY_INPUT="$1"; shift ;;
    --specstory-path=*)
      SPECSTORY_PATH_SET=1; SPECSTORY_INPUT="${1#--specstory-path=}"
      [ -n "$SPECSTORY_INPUT" ] || die "--specstory-path cannot be empty" 1
      shift ;;
    --newest) USE_NEWEST=1; shift ;;
    --format)
      shift
      [ $# -gt 0 ] && [ -n "$1" ] || die "--format needs specstory, claude, or both" 1
      FORMAT="$1"; shift ;;
    --format=*)
      FORMAT="${1#--format=}"
      [ -n "$FORMAT" ] || die "--format cannot be empty" 1
      shift ;;
    --json) OUT_JSON=1; shift ;;
    --quiet) QUIET=1; shift ;;
    --help|-h) usage; exit 0 ;;
    -*) die "unknown flag (try --help)" 1 ;;
    *)  die "unexpected positional argument (try --help)" 1 ;;
  esac
done

case "$FORMAT" in
  both|specstory|claude) ;;
  *) die "invalid --format (expected both|specstory|claude)" 1 ;;
esac
if [ "$USE_NEWEST" = "1" ] && { [ "$SESSION_ID_SET" = "1" ] || [ "$SPECSTORY_PATH_SET" = "1" ]; }; then
  die "--newest cannot be combined with exact selectors" 1
fi
if [ "$USE_NEWEST" = "0" ] && [ "$SESSION_ID_SET" = "0" ] && [ "$SPECSTORY_PATH_SET" = "0" ]; then
  die "exact selection requires --session-id or --specstory-path (use --newest only for compatibility)" 1
fi
# Never reflect an invalid selector: it can be arbitrary transcript-captured text.
if [ "$SESSION_ID_SET" = "1" ] && ! printf '%s\n' "$SESSION_ID" | grep -Eq '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'; then
  die "invalid --session-id: expected a canonical lowercase UUID (value not shown)" 1
fi

STATUS="error"
CONFIDENCE="none"
SOURCE=""
SPECSTORY_PATH=""
CLAUDE_JSONL=""
CLAUDE_UUID="$SESSION_ID"
CANDIDATES=()

if [ "$USE_NEWEST" = "1" ]; then
  SOURCE="newest"
elif [ "$SESSION_ID_SET" = "1" ] && [ "$SPECSTORY_PATH_SET" = "1" ]; then
  SOURCE="session-id+specstory-path"
elif [ "$SESSION_ID_SET" = "1" ]; then
  SOURCE="session-id"
else
  SOURCE="specstory-path"
fi

has_control_bytes() {
  case "$1" in *$'\n'*|*$'\r'*|*$'\t'*) return 0 ;; esac
  LC_ALL=C printf '%s' "$1" | LC_ALL=C grep -q '[[:cntrl:]]'
}

is_valid_utf8() {
  command -v iconv >/dev/null 2>&1 || return 1
  printf '%s' "$1" | iconv -f UTF-8 -t UTF-8 >/dev/null 2>&1
}

output_fields_are_utf8() {
  command -v iconv >/dev/null 2>&1 || return 1
  {
    printf '%s\0' "$STATUS" "$CONFIDENCE" "$SOURCE" "$SPECSTORY_PATH" "$CLAUDE_UUID" "$CLAUDE_JSONL"
    printf '%s\0' "${CANDIDATES[@]:-}"
  } | iconv -f UTF-8 -t UTF-8 >/dev/null 2>&1
}

json_escape() {
  # All C0 controls and non-UTF-8 bytes are rejected before this point.
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

sanitize_output_fields() {
  local candidate unsafe=0 value
  for value in "$STATUS" "$CONFIDENCE" "$SOURCE" "$SPECSTORY_PATH" "$CLAUDE_UUID" "$CLAUDE_JSONL"; do
    has_control_bytes "$value" && unsafe=1
  done
  for candidate in "${CANDIDATES[@]:-}"; do
    has_control_bytes "$candidate" && unsafe=1
  done
  output_fields_are_utf8 || unsafe=1
  if [ "$unsafe" = "1" ]; then
    STATUS="mismatch"
    CONFIDENCE="none"
    SPECSTORY_PATH=""
    CLAUDE_JSONL=""
    CLAUDE_UUID=""
    CANDIDATES=()
    log "error: unsafe control byte rejected from structured output (value not shown)"
  fi
}

emit_result() {
  local candidate first
  sanitize_output_fields
  if [ "$OUT_JSON" = "1" ]; then
    printf '{"status":"%s","confidence":"%s","source":"%s"' \
      "$(json_escape "$STATUS")" "$(json_escape "$CONFIDENCE")" "$(json_escape "$SOURCE")"
    printf ',"specstory_path":"%s"' "$(json_escape "$SPECSTORY_PATH")"
    printf ',"claude_session_uuid":"%s"' "$(json_escape "$CLAUDE_UUID")"
    printf ',"claude_jsonl_path":"%s","candidates":[' "$(json_escape "$CLAUDE_JSONL")"
    first=1
    for candidate in "${CANDIDATES[@]:-}"; do
      [ -n "$candidate" ] || continue
      [ "$first" = "1" ] || printf ','
      printf '"%s"' "$(json_escape "$candidate")"
      first=0
    done
    printf ']}\n'
  else
    printf 'status\t%s\n' "$STATUS"
    printf 'confidence\t%s\n' "$CONFIDENCE"
    printf 'source\t%s\n' "$SOURCE"
    printf 'specstory_path\t%s\n' "$SPECSTORY_PATH"
    printf 'claude_session_uuid\t%s\n' "$CLAUDE_UUID"
    printf 'claude_jsonl_path\t%s\n' "$CLAUDE_JSONL"
    for candidate in "${CANDIDATES[@]:-}"; do
      [ -n "$candidate" ] && printf 'candidate\t%s\n' "$candidate"
    done
  fi
  return 0
}

fail_result() {
  STATUS="$1"
  CONFIDENCE="none"
  local message="$2" rc="$3" candidate
  sanitize_output_fields
  log "error: $message"
  if [ "${#CANDIDATES[@]}" -gt 0 ]; then
    log "candidates:"
    for candidate in "${CANDIDATES[@]}"; do log "  $candidate"; done
  fi
  emit_result
  exit "$rc"
}

if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
  fail_result "error" "not inside a git repository" 2
fi
REPO_ROOT="$(git rev-parse --show-toplevel)"
SPECSTORY_DIR="$REPO_ROOT/.specstory/history"
PROJECTS_DIR="$HOME/.claude/projects"

command -v iconv >/dev/null 2>&1 || fail_result "dependency_error" "iconv is required for safe structured path output" 6
if has_control_bytes "$REPO_ROOT" || has_control_bytes "$HOME" || \
   ! is_valid_utf8 "$REPO_ROOT" || ! is_valid_utf8 "$HOME"; then
  fail_result "mismatch" "repository or home path is not safe UTF-8 text (value not shown)" 5
fi
if [ "$SPECSTORY_PATH_SET" = "1" ] && \
   { has_control_bytes "$SPECSTORY_INPUT" || ! is_valid_utf8 "$SPECSTORY_INPUT"; }; then
  fail_result "mismatch" "SpecStory selector path is not safe UTF-8 text (value not shown)" 5
fi

cwd_slug() {
  printf '%s' "$REPO_ROOT" | sed 's|[^A-Za-z0-9]|-|g'
}

canonical_existing_file() {
  local input="$1" absolute parent base
  case "$input" in
    /*) absolute="$input" ;;
    *)  absolute="$REPO_ROOT/$input" ;;
  esac
  [ ! -L "$absolute" ] || return 2
  [ -f "$absolute" ] || return 1
  parent="$(cd "$(dirname "$absolute")" 2>/dev/null && pwd -P)" || return 1
  base="$(basename "$absolute")"
  printf '%s/%s' "$parent" "$base"
}

specstory_root_is_canonical() {
  local canonical
  [ -d "$SPECSTORY_DIR" ] || return 1
  [ ! -L "$REPO_ROOT/.specstory" ] || return 2
  [ ! -L "$SPECSTORY_DIR" ] || return 2
  canonical="$(cd "$SPECSTORY_DIR" 2>/dev/null && pwd -P)" || return 1
  [ "$canonical" = "$SPECSTORY_DIR" ] || return 2
}

# Read a fixed 8 KiB byte prefix, then inspect at most the eight-line SpecStory
# v2.1 prologue window. This stays bounded even if the first body line has no
# newline and fills the rest of a huge/sparse file.
specstory_header_uuid() {
  local path="$1" marker_re title_re prefix="" line marker_uuid="" marker_count=0 index=0
  local lines=()
  marker_re='^<!-- Claude Code Session ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) \([^)]*\) -->$'
  # --local-time-zone can render offsets such as -0700; line position and a
  # nonempty Markdown title are stable, but UTC-only timestamp text is not.
  title_re='^# .+$'

  LC_ALL=C IFS= read -r -d '' -n 8192 prefix < "$path" || true
  while [ "$index" -lt 8 ] && IFS= read -r line; do
    lines+=("$line")
    if [[ "$line" =~ $marker_re ]]; then
      marker_count=$((marker_count + 1))
      marker_uuid="${BASH_REMATCH[1]}"
    fi
    index=$((index + 1))
  done <<EOF
$prefix
EOF

  [ "${#lines[@]}" -ge 6 ] || return 1
  [ "${lines[0]}" = '<!-- Generated by SpecStory, Markdown v2.1.0 -->' ] || return 1
  [ -z "${lines[1]}" ] || return 1
  [[ "${lines[2]}" =~ $title_re ]] || return 1
  [ -z "${lines[3]}" ] || return 1
  [[ "${lines[4]}" =~ $marker_re ]] || return 1
  [ -z "${lines[5]}" ] || return 1
  [ "$marker_count" = "1" ] || return 1
  [ "$marker_uuid" = "${BASH_REMATCH[1]}" ] || return 1
  printf '%s' "$marker_uuid"
}

validate_specstory_path() {
  local path="$1" parent uuid root_rc=0
  specstory_root_is_canonical || root_rc=$?
  [ "$root_rc" = "0" ] || return 2
  [ ! -L "$path" ] || return 2
  [ -f "$path" ] || return 1
  parent="$(cd "$(dirname "$path")" 2>/dev/null && pwd -P)" || return 2
  [ "$parent" = "$SPECSTORY_DIR" ] || return 2
  case "$(basename "$path")" in *.md) ;; *) return 2 ;; esac
  uuid="$(specstory_header_uuid "$path")" || return 3
  [ -n "$uuid" ] || return 3
  printf '%s' "$uuid"
}

validate_claude_jsonl() {
  local path="$1" uuid="$2"
  command -v python3 >/dev/null 2>&1 || return 3
  python3 - "$path" "$uuid" "$REPO_ROOT" <<'PY'
import json
import os
import subprocess
import sys

path, expected_session, expected_root = sys.argv[1:]
expected_root = os.path.realpath(expected_root)
matched = False
session_roots = set()
root_cache = {}


def git_root(cwd):
    if not isinstance(cwd, str) or not cwd:
        return None
    if cwd in root_cache:
        return root_cache[cwd]
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        value = os.path.realpath(result.stdout.rstrip("\n"))
    except (OSError, subprocess.SubprocessError):
        value = None
    root_cache[cwd] = value
    return value


try:
    with open(path, "rb") as stream:
        for raw in stream:
            try:
                text = raw.decode("utf-8")
                value = json.loads(text)
            except (UnicodeDecodeError, json.JSONDecodeError):
                # An active writer's incomplete final record is still ambiguous:
                # fail closed and ask the caller to retry after it is quiescent.
                sys.exit(2)
            if not isinstance(value, dict):
                sys.exit(2)
            if value.get("sessionId") == expected_session:
                root = git_root(value.get("cwd"))
                if root is not None:
                    session_roots.add(root)
                    if root == expected_root:
                        matched = True
except (OSError, UnicodeError):
    sys.exit(2)

if len(session_roots) > 1:
    sys.exit(4)
sys.exit(0 if matched else 1)
PY
}

newest_file() {
  local dir="$1" glob="$2" newest="" file
  [ -d "$dir" ] || { printf ''; return 0; }
  while IFS= read -r -d '' file; do
    if [ -z "$newest" ] || [ "$file" -nt "$newest" ]; then newest="$file"; fi
  done < <(find "$dir" -maxdepth 1 -type f -name "$glob" -print0 2>/dev/null)
  printf '%s' "$newest"
}

append_safe_candidate() {
  local path="$1"
  if has_control_bytes "$path" || ! is_valid_utf8 "$path"; then
    CANDIDATES=()
    fail_result "mismatch" "candidate path is not safe UTF-8 text (value not shown)" 5
  fi
  CANDIDATES+=("$path")
}

resolve_exact_specstory() {
  local selected_uuid="" selected_path="" file uuid path_rc=0 root_rc=0
  specstory_root_is_canonical || root_rc=$?
  if [ "$root_rc" = "1" ]; then
    fail_result "not_found" "no canonical .specstory/history directory exists under the git root" 3
  elif [ "$root_rc" != "0" ]; then
    fail_result "mismatch" ".specstory/history must be a canonical in-repository directory, not a symlink" 5
  fi

  if [ "$SPECSTORY_PATH_SET" = "1" ]; then
    selected_path="$(canonical_existing_file "$SPECSTORY_INPUT")" || path_rc=$?
    case "$path_rc" in
      0) ;;
      2) fail_result "mismatch" "--specstory-path must not be a symlink (value not shown)" 5 ;;
      *) fail_result "not_found" "--specstory-path does not name an existing regular file (value not shown)" 3 ;;
    esac
    selected_uuid="$(validate_specstory_path "$selected_path")" || path_rc=$?
    [ "$path_rc" = "0" ] || fail_result "mismatch" "--specstory-path must be a direct child with the fixed SpecStory v2.1 prologue and lowercase Claude UUID (value not shown)" 5
    if [ "$SESSION_ID_SET" = "1" ] && [ "$selected_uuid" != "$SESSION_ID" ]; then
      fail_result "mismatch" "SpecStory header UUID does not equal the selected session UUID (selector values not shown)" 5
    fi
    SPECSTORY_PATH="$selected_path"
    CLAUDE_UUID="$selected_uuid"
    CANDIDATES=()
    return 0
  fi

  while IFS= read -r -d '' file; do
    uuid="$(specstory_header_uuid "$file" 2>/dev/null || true)"
    if [ "$uuid" = "$SESSION_ID" ]; then append_safe_candidate "$file"; fi
  done < <(find "$SPECSTORY_DIR" -maxdepth 1 -type f -name '*.md' -print0 2>/dev/null)

  case "${#CANDIDATES[@]}" in
    0) fail_result "not_found" "no direct-child SpecStory transcript has the exact selected UUID" 3 ;;
    1) SPECSTORY_PATH="${CANDIDATES[0]}"; CANDIDATES=() ;;
    *) fail_result "ambiguous" "multiple SpecStory transcripts have the selected UUID; retry with --specstory-path" 4 ;;
  esac
}

resolve_exact_claude() {
  local candidate validation_rc=0 malformed=0 conflicting_roots=0
  local seen=() matches=()
  [ -n "$CLAUDE_UUID" ] || fail_result "mismatch" "no Claude session UUID could be derived from the exact selector" 5
  [ -d "$PROJECTS_DIR" ] || fail_result "not_found" "no Claude project stores exist under the configured home" 3

  while IFS= read -r -d '' candidate; do
    if has_control_bytes "$candidate" || ! is_valid_utf8 "$candidate"; then
      CANDIDATES=()
      fail_result "mismatch" "Claude JSONL candidate path is not safe UTF-8 text (value not shown)" 5
    fi
    seen+=("$candidate")
    if [ -L "$candidate" ]; then
      CANDIDATES=("$candidate")
      fail_result "mismatch" "exact Claude JSONL candidates must not be symlinks" 5
    fi
    [ -f "$candidate" ] || continue
    validation_rc=0
    validate_claude_jsonl "$candidate" "$CLAUDE_UUID" || validation_rc=$?
    case "$validation_rc" in
      0) matches+=("$candidate") ;;
      1) ;;
      2) malformed=1 ;;
      3) fail_result "dependency_error" "python3 is required only for exact Claude JSONL validation" 6 ;;
      4) conflicting_roots=1 ;;
      *) malformed=1 ;;
    esac
  done < <(find "$PROJECTS_DIR" -mindepth 2 -maxdepth 2 -name "$CLAUDE_UUID.jsonl" -print0 2>/dev/null)

  if [ "$malformed" = "1" ]; then
    CANDIDATES=("${seen[@]}")
    fail_result "mismatch" "a Claude JSONL candidate is malformed or incomplete; retry after the writer is quiescent" 5
  fi
  if [ "$conflicting_roots" = "1" ]; then
    CANDIDATES=("${seen[@]}")
    fail_result "mismatch" "one Claude JSONL session contains conflicting canonical git roots" 5
  fi
  case "${#matches[@]}" in
    0)
      CANDIDATES=("${seen[@]}")
      if [ "${#seen[@]}" -eq 0 ]; then
        fail_result "not_found" "no exact UUID-named Claude JSONL candidate was found" 3
      fi
      fail_result "mismatch" "no Claude JSONL candidate proves the selected session and canonical worktree root" 5 ;;
    1) CLAUDE_JSONL="${matches[0]}"; CANDIDATES=() ;;
    *) CANDIDATES=("${matches[@]}"); fail_result "ambiguous" "multiple Claude JSONL candidates prove the selected session and worktree root" 4 ;;
  esac
}

resolve_newest() {
  local project_dir header_uuid
  if [ "$FORMAT" = "both" ] || [ "$FORMAT" = "specstory" ]; then
    SPECSTORY_PATH="$(newest_file "$SPECSTORY_DIR" '*.md')"
    [ -n "$SPECSTORY_PATH" ] || fail_result "not_found" "no .specstory/history/*.md under the git root" 3
    { has_control_bytes "$SPECSTORY_PATH" || ! is_valid_utf8 "$SPECSTORY_PATH"; } && fail_result "mismatch" "newest SpecStory path is not safe UTF-8 text" 5
    header_uuid="$(specstory_header_uuid "$SPECSTORY_PATH" 2>/dev/null || true)"
    [ -n "$header_uuid" ] && CLAUDE_UUID="$header_uuid"
  fi
  if [ "$FORMAT" = "both" ] || [ "$FORMAT" = "claude" ]; then
    project_dir="$HOME/.claude/projects/$(cwd_slug)"
    CLAUDE_JSONL="$(newest_file "$project_dir" '*.jsonl')"
    [ -n "$CLAUDE_JSONL" ] || fail_result "not_found" "no Claude JSONL under the git-root-derived compatibility store" 3
    { has_control_bytes "$CLAUDE_JSONL" || ! is_valid_utf8 "$CLAUDE_JSONL"; } && fail_result "mismatch" "newest Claude JSONL path is not safe UTF-8 text" 5
    CLAUDE_UUID="$(basename "$CLAUDE_JSONL" .jsonl)"
  fi
}

if [ "$USE_NEWEST" = "1" ]; then
  resolve_newest
  STATUS="resolved"
  CONFIDENCE="heuristic"
else
  if [ "$FORMAT" = "both" ] || [ "$FORMAT" = "specstory" ] || [ "$SPECSTORY_PATH_SET" = "1" ]; then
    resolve_exact_specstory
    [ "$FORMAT" = "claude" ] && SPECSTORY_PATH=""
  fi
  if [ "$FORMAT" = "both" ] || [ "$FORMAT" = "claude" ]; then resolve_exact_claude; fi
  STATUS="resolved"
  CONFIDENCE="exact"
fi

emit_result
