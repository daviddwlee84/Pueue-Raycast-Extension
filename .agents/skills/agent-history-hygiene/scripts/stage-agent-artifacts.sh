#!/usr/bin/env bash
# stage-agent-artifacts.sh — git-add validated agent artifacts before a commit.
#
# Exact session-only mode is fail-closed. The default broad mode preserves the
# historical branch-wide behavior for callers that intentionally want it.
# Bash 3.2 compatible (stock macOS).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_DIRS_FILE="$SKILL_DIR/assets/artifact-dirs.txt"
REDACTOR="$SKILL_DIR/assets/redact_secrets.py"
PYTHON_BIN=python3

usage() {
  cat <<'EOF'
Usage: stage-agent-artifacts.sh [OPTIONS]

Stage agent artifacts for the next commit. Must run inside a git repository.
The default is broad branch-wide compatibility mode: every dirty Markdown file
under the configured artifact directories. Use --session-only for exact mode.

Exact session-only options:
  --session-only          Select only one exact transcript plus an explicit plan.
  --check-staged          Validation-only: require selected artifacts and staged
                          non-artifact code in the current GIT_INDEX_FILE. Never
                          mutate an index. Requires --session-only.
  --session-id UUID       Exact Claude Code session UUID.
  --specstory-path PATH   Exact .specstory/history/*.md path (relative paths are
                          resolved from the git root). May accompany UUID.
  --no-specstory          Intentionally omit rendered SpecStory history. Requires
                          --session-id so the raw Claude JSONL can still be proved.
  --plan PATH             Stage this exact in-repo Markdown plan.
  --no-plan               Explicitly state that this session has no plan.
  --sanitize-index        Sanitize only the exact selected blobs inside the
                          locked alternate index, then require a clean check.
                          Direct use is finalizer/quiescence-only: an advisory
                          hash cannot stop an arbitrary live writer.
  --materialize-sanitized Verify every selected live generation even when no
                          bytes change; when bytes do change, materialize with
                          old-inode backups and post-checks. Finalizer/
                          quiescence-only. Requires --sanitize-index.
  --gitleaks-config PATH  Trusted scanner policy used by whichever scanning pass
                          the selected exact mode runs, instead of the worktree
                          .gitleaks.toml. The finalizer pins this to the
                          request's own HEAD so a live session cannot weaken
                          the policy mid-run. Requires --session-only.
  --receipt-request PATH V2 finalizer-only private request. Require whole prepared
                          index scanner coverage and durable complete beforeimage/
                          transformation evidence before source/index publication.
                          Requires materialized exact sanitation and owned lock.
  --expect-index-tree OID Refuse to touch the index unless it currently writes
                          exactly this tree. Proves the queued staged tree is
                          still the one being finalized. Requires
                          --session-only.

Broad-mode options:
  --include-all-plans     Re-add already-staged modified artifact files instead
                          of considering only unstaged/untracked files.
  --dirs-file PATH        Override artifact-dirs.txt.

Common options:
  --dry-run               Validate and print one proposed git-add set, but do not
                          change the index.
  --allow-empty           Allow artifact-only staging when there are no code
                          changes (default refuses accidental transcript-only work).
  --help, -h              Show this help and exit.

Exit codes:
  0  staging/check success; sanitizer made no replacements
  10 exact blobs were sanitized and credential rotation is required
  1  invalid arguments
  2  not inside a git repository
  3  no code changes and no dirty artifacts
  4  artifacts are dirty but code is clean (use --allow-empty)
  5  exact selector/path validation failed; index is unchanged
  6  --expect-index-tree mismatch: the staged tree moved since queueing
EOF
}

log()  { printf '%s\n' "$*" >&2; }
warn() { printf 'warn: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$1" >&2; exit "${2:-1}"; }
has_control_bytes() {
  case "$1" in *$'\n'*|*$'\r'*|*$'\t'*) return 0 ;; esac
  LC_ALL=C printf '%s' "$1" | LC_ALL=C grep -q '[[:cntrl:]]'
}
is_valid_utf8() {
  command -v iconv >/dev/null 2>&1 || return 1
  printf '%s' "$1" | iconv -f UTF-8 -t UTF-8 >/dev/null 2>&1
}

SESSION_ONLY=0
CHECK_STAGED=0
INCLUDE_ALL_PLANS=0
DIRS_FILE="$DEFAULT_DIRS_FILE"
DRY_RUN=0
ALLOW_EMPTY=0
SESSION_ID=""
SESSION_ID_SET=0
SPECSTORY_INPUT=""
SPECSTORY_PATH_SET=0
NO_SPECSTORY=0
PLAN_INPUT=""
PLAN_SET=0
NO_PLAN=0
SANITIZE_INDEX=0
MATERIALIZE_SANITIZED=0
GITLEAKS_CONFIG=""
GITLEAKS_CONFIG_SET=0
EXPECT_INDEX_TREE=""
EXPECT_INDEX_TREE_SET=0
RECEIPT_REQUEST=""

while [ $# -gt 0 ]; do
  case "$1" in
    --session-only) SESSION_ONLY=1; shift ;;
    --check-staged) CHECK_STAGED=1; shift ;;
    --session-id)
      shift
      [ $# -gt 0 ] && [ -n "$1" ] || die "--session-id needs a non-empty UUID (value not shown)" 1
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
    --no-specstory) NO_SPECSTORY=1; shift ;;
    --plan)
      shift
      [ $# -gt 0 ] && [ -n "$1" ] || die "--plan needs a non-empty path (value not shown)" 1
      PLAN_SET=1; PLAN_INPUT="$1"; shift ;;
    --plan=*)
      PLAN_SET=1; PLAN_INPUT="${1#--plan=}"
      [ -n "$PLAN_INPUT" ] || die "--plan cannot be empty" 1
      shift ;;
    --no-plan) NO_PLAN=1; shift ;;
    --receipt-request)
      shift
      [ $# -gt 0 ] && [ -n "$1" ] || die "--receipt-request requires an absolute private request" 1
      [ -z "$RECEIPT_REQUEST" ] || die "--receipt-request may be passed only once" 1
      RECEIPT_REQUEST="$1"; shift ;;
    --sanitize-index) SANITIZE_INDEX=1; shift ;;
    --materialize-sanitized) MATERIALIZE_SANITIZED=1; shift ;;
    --gitleaks-config)
      shift
      [ $# -gt 0 ] && [ -n "$1" ] || die "--gitleaks-config needs a non-empty path (value not shown)" 1
      GITLEAKS_CONFIG_SET=1; GITLEAKS_CONFIG="$1"; shift ;;
    --gitleaks-config=*)
      GITLEAKS_CONFIG_SET=1; GITLEAKS_CONFIG="${1#--gitleaks-config=}"
      [ -n "$GITLEAKS_CONFIG" ] || die "--gitleaks-config cannot be empty" 1
      shift ;;
    --expect-index-tree)
      shift
      [ $# -gt 0 ] && [ -n "$1" ] || die "--expect-index-tree needs a non-empty object id" 1
      EXPECT_INDEX_TREE_SET=1; EXPECT_INDEX_TREE="$1"; shift ;;
    --expect-index-tree=*)
      EXPECT_INDEX_TREE_SET=1; EXPECT_INDEX_TREE="${1#--expect-index-tree=}"
      [ -n "$EXPECT_INDEX_TREE" ] || die "--expect-index-tree cannot be empty" 1
      shift ;;
    --include-all-plans) INCLUDE_ALL_PLANS=1; shift ;;
    --dirs-file)
      shift
      [ $# -gt 0 ] && [ -n "$1" ] || die "--dirs-file needs a non-empty path (value not shown)" 1
      DIRS_FILE="$1"; shift ;;
    --dirs-file=*)
      DIRS_FILE="${1#--dirs-file=}"
      [ -n "$DIRS_FILE" ] || die "--dirs-file cannot be empty" 1
      shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --allow-empty) ALLOW_EMPTY=1; shift ;;
    --help|-h) usage; exit 0 ;;
    -*) die "unknown flag: $1 (try --help)" 1 ;;
    *)  die "unexpected positional arg: $1 (try --help)" 1 ;;
  esac
done

command -v iconv >/dev/null 2>&1 || die "iconv is required for safe artifact path handling" 1
if [ "$SESSION_ID_SET" = "1" ] && \
   { has_control_bytes "$SESSION_ID" || ! is_valid_utf8 "$SESSION_ID"; }; then
  die "--session-id is not safe UTF-8 text (value not shown)" 1
fi
if [ "$SPECSTORY_PATH_SET" = "1" ] && \
   { has_control_bytes "$SPECSTORY_INPUT" || ! is_valid_utf8 "$SPECSTORY_INPUT"; }; then
  die "--specstory-path is not safe UTF-8 text (value not shown)" 1
fi
if [ "$PLAN_SET" = "1" ] && \
   { has_control_bytes "$PLAN_INPUT" || ! is_valid_utf8 "$PLAN_INPUT"; }; then
  die "--plan is not safe UTF-8 text (value not shown)" 1
fi
if has_control_bytes "$DIRS_FILE" || ! is_valid_utf8 "$DIRS_FILE"; then
  die "--dirs-file is not safe UTF-8 text (value not shown)" 1
fi
if [ "$GITLEAKS_CONFIG_SET" = "1" ]; then
  if has_control_bytes "$GITLEAKS_CONFIG" || ! is_valid_utf8 "$GITLEAKS_CONFIG"; then
    die "--gitleaks-config is not safe UTF-8 text (value not shown)" 1
  fi
  case "$GITLEAKS_CONFIG" in
    /*) ;;
    *) die "--gitleaks-config must be an absolute path" 1 ;;
  esac
  [ -f "$GITLEAKS_CONFIG" ] || die "--gitleaks-config must name an existing regular file" 1
  # -f follows symlinks, so reject the link itself only after proving a regular
  # target. An `if` rather than `[ ... ] && die` so the false branch can never
  # become a nonzero statement result under `set -e`.
  if [ -L "$GITLEAKS_CONFIG" ]; then
    die "--gitleaks-config must not be a symlink" 1
  fi
fi
if [ "$EXPECT_INDEX_TREE_SET" = "1" ]; then
  case "${#EXPECT_INDEX_TREE}" in 40|64) ;;
    *) die "--expect-index-tree must be a full-length object id" 1 ;;
  esac
  case "$EXPECT_INDEX_TREE" in
    *[!0-9a-f]*) die "--expect-index-tree must be lowercase hexadecimal" 1 ;;
  esac
fi

if [ -n "$RECEIPT_REQUEST" ]; then
  PYTHON_BIN="${AGENT_HISTORY_PYTHON:-python3}"
  [ "$MATERIALIZE_SANITIZED" = "1" ] || die "receipt requires materialized exact sanitation" 1
  case "$RECEIPT_REQUEST" in /*) ;; *) die "receipt request must be absolute" 1 ;; esac
  if has_control_bytes "$RECEIPT_REQUEST" || ! is_valid_utf8 "$RECEIPT_REQUEST"; then
    die "receipt request path is unsafe" 1
  fi
fi
[ "$MATERIALIZE_SANITIZED" = "0" ] || [ "$SANITIZE_INDEX" = "1" ] || \
  die "--materialize-sanitized requires --sanitize-index" 1
[ "$GITLEAKS_CONFIG_SET" = "0" ] || [ "$SESSION_ONLY" = "1" ] || \
  die "--gitleaks-config requires exact --session-only mode" 1
[ "$EXPECT_INDEX_TREE_SET" = "0" ] || [ "$SESSION_ONLY" = "1" ] || \
  die "--expect-index-tree requires exact --session-only mode" 1
if [ "$SANITIZE_INDEX" = "1" ]; then
  [ "$SESSION_ONLY" = "1" ] || die "--sanitize-index requires exact --session-only mode" 1
  [ "$CHECK_STAGED" = "0" ] || die "--sanitize-index cannot be combined with --check-staged" 1
  [ "$DRY_RUN" = "0" ] || die "--sanitize-index cannot be combined with --dry-run" 1
  [ "$INCLUDE_ALL_PLANS" = "0" ] || die "--sanitize-index cannot be combined with --include-all-plans" 1
  command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "python3 is required for index sanitation" 5
  [ -f "$REDACTOR" ] || die "the staged artifact sanitizer is unavailable" 5
fi

if [ "$CHECK_STAGED" = "1" ]; then
  [ "$SESSION_ONLY" = "1" ] || die "--check-staged requires --session-only" 1
  [ "$DRY_RUN" = "0" ] || die "--check-staged cannot be combined with --dry-run" 1
  [ "$ALLOW_EMPTY" = "0" ] || die "--check-staged cannot be combined with --allow-empty" 1
  [ "$INCLUDE_ALL_PLANS" = "0" ] || die "--check-staged cannot be combined with --include-all-plans" 1
fi

if [ "$SESSION_ONLY" = "0" ]; then
  if [ "$SESSION_ID_SET" = "1" ] || [ "$SPECSTORY_PATH_SET" = "1" ] || [ "$NO_SPECSTORY" = "1" ] || \
     [ "$PLAN_SET" = "1" ] || [ "$NO_PLAN" = "1" ]; then
    die "exact selectors require --session-only (omit them to use broad branch-wide mode)" 1
  fi
else
  [ "$INCLUDE_ALL_PLANS" = "0" ] || die "--include-all-plans applies only to broad mode" 1
  if [ "$PLAN_SET" = "1" ] && [ "$NO_PLAN" = "1" ]; then
    die "choose exactly one of --plan PATH or --no-plan" 1
  fi
  if [ "$PLAN_SET" = "0" ] && [ "$NO_PLAN" = "0" ]; then
    die "--session-only requires an explicit --plan PATH or --no-plan decision" 1
  fi
  if [ "$NO_SPECSTORY" = "1" ] && [ "$SPECSTORY_PATH_SET" = "1" ]; then
    die "--no-specstory cannot be combined with --specstory-path" 1
  fi
  if [ "$NO_SPECSTORY" = "1" ] && [ "$SESSION_ID_SET" = "0" ]; then
    die "--no-specstory requires --session-id so exact raw-session identity can be validated" 1
  fi
  if [ "$NO_SPECSTORY" = "0" ] && [ "$SESSION_ID_SET" = "0" ] && [ "$SPECSTORY_PATH_SET" = "0" ]; then
    die "--session-only requires --session-id or --specstory-path (or --session-id with --no-specstory)" 1
  fi
fi

if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
  die "not inside a git repository" 2
fi
REPO_ROOT="$(git rev-parse --show-toplevel)"
INVOCATION_DIR="$PWD"
cd "$REPO_ROOT"

# Preserve a caller-supplied dirs file relative to the invocation directory.
case "$DIRS_FILE" in
  /*) ;;
  *) DIRS_FILE="$INVOCATION_DIR/$DIRS_FILE" ;;
esac
[ -f "$DIRS_FILE" ] || die "artifact-dirs.txt was not found (path not shown)" 1

ARTIFACT_DIRS=()
while IFS= read -r line; do
  line="${line%%#*}"
  line="$(printf '%s' "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  while [ "${line%/}" != "$line" ]; do line="${line%/}"; done
  [ -n "$line" ] && ARTIFACT_DIRS+=("$line")
done < "$DIRS_FILE"

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

repo_relative_path() {
  local absolute="$1"
  case "$absolute" in
    "$REPO_ROOT"/*) printf '%s' "${absolute#"$REPO_ROOT"/}" ;;
    *) return 1 ;;
  esac
}

is_configured_artifact_path() {
  local path="$1" dir
  for dir in "${ARTIFACT_DIRS[@]}"; do
    case "$path" in "$dir"/*) return 0 ;; esac
  done
  return 1
}

is_exact_plan_path() {
  local path="$1"
  is_configured_artifact_path "$path" || return 1
  # The transcript namespace can be configured for broad collection but cannot
  # be mislabeled as an exact plan.
  case "$path" in .specstory/history/*) return 1 ;; esac
  return 0
}

validate_candidate_path() {
  local path="$1"
  if has_control_bytes "$path"; then
    die "artifact path contains unsupported control bytes (value not shown)" 5
  fi
  case "$path" in
    /*|../*|*/../*|*/..|.|..) die "artifact path is not a safe repo-relative path (value not shown)" 5 ;;
    *.md) ;;
    *) die "artifact path must be Markdown (value not shown)" 5 ;;
  esac
  is_configured_artifact_path "$path" || die "artifact path is outside configured directories (value not shown)" 5
  if [ -e "$path" ]; then
    [ -f "$path" ] || die "artifact path is not a regular file (value not shown)" 5
    [ ! -L "$path" ] || die "artifact path must not be a symlink (value not shown)" 5
  elif ! GIT_LITERAL_PATHSPECS=1 git ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
    die "artifact path does not exist and is not tracked (value not shown)" 5
  fi
}

append_unique_candidate() {
  local path="$1" existing
  for existing in "${CANDIDATES[@]:-}"; do
    [ "$existing" = "$path" ] && return 0
  done
  CANDIDATES+=("$path")
}

status_needs_add() {
  local status="$1" index_status worktree_status
  [ "$status" = "??" ] && return 0
  index_status="${status:0:1}"
  worktree_status="${status:1:1}"
  [ "$worktree_status" != " " ] && return 0
  [ "$INCLUDE_ALL_PLANS" = "1" ] && [ "$index_status" != " " ] && return 0
  return 1
}

# One porcelain snapshot for every configured directory. Rename/copy origins are
# consumed as part of their preceding XY record and are never reclassified as a
# standalone path.
collect_broad_artifacts() {
  local entry status destination origin=""
  while IFS= read -r -d '' entry; do
    [ "${#entry}" -ge 3 ] || die "malformed git status record while collecting artifacts" 5
    status="${entry:0:2}"
    destination="${entry:3}"
    origin=""
    case "$status" in
      DD|AU|UD|UA|DU|AA|UU)
        case "$destination" in
          *.md) is_configured_artifact_path "$destination" && \
            die "unmerged artifact conflicts must be resolved explicitly; index unchanged" 5 ;;
        esac
        continue ;;
    esac
    case "$status" in
      *R*|*C*)
        IFS= read -r -d '' origin || die "incomplete rename/copy record from git status" 5 ;;
    esac
    status_needs_add "$status" || continue
    case "$destination" in
      *.md) is_configured_artifact_path "$destination" && append_unique_candidate "$destination" ;;
    esac
    if [ "${status:1:1}" = "R" ]; then
      case "$origin" in
        *.md) is_configured_artifact_path "$origin" && append_unique_candidate "$origin" ;;
      esac
    fi
  done < <(git -c core.quotePath=false status --porcelain=v1 -z -uall 2>/dev/null)
}

# The feature-diff guard is about what the next commit will contain, not merely
# dirty working-tree files. Parse staged rename/copy records as paired records.
has_staged_code_changes() {
  local status path origin destination
  while IFS= read -r -d '' status; do
    case "$status" in
      U*)
        IFS= read -r -d '' path || return 1
        continue ;;
      R*)
        IFS= read -r -d '' origin || return 1
        IFS= read -r -d '' destination || return 1
        if ! is_configured_artifact_path "$origin" || ! is_configured_artifact_path "$destination"; then return 0; fi ;;
      C*)
        IFS= read -r -d '' origin || return 1
        IFS= read -r -d '' destination || return 1
        # A copy changes only the destination; its origin is context, not a
        # separately staged code path.
        is_configured_artifact_path "$destination" || return 0 ;;
      *)
        IFS= read -r -d '' path || return 1
        is_configured_artifact_path "$path" || return 0 ;;
    esac
  done < <(git -c core.quotePath=false diff --cached --name-status -z --find-renames --find-copies --)
  return 1
}

INDEX_TRANSACTION_ACTIVE=0
INDEX_PATH=""
INDEX_LOCK_PATH=""
TEMP_INDEX_PATH=""
MATERIALIZATION_STARTED=0
MATERIALIZE_BACKUPS=()
MATERIALIZE_BACKUP_IDS=()
MATERIALIZE_BACKUP_ACTIVE=()
MATERIALIZE_RECOVERY_LEFT=0

cleanup_index_transaction() {
  if [ "$INDEX_TRANSACTION_ACTIVE" = "1" ]; then
    [ -z "$TEMP_INDEX_PATH" ] || rm -f "$TEMP_INDEX_PATH"
    [ -z "$INDEX_LOCK_PATH" ] || rm -f "$INDEX_LOCK_PATH"
    INDEX_TRANSACTION_ACTIVE=0
  fi
}

absolute_git_path() {
  case "$1" in
    /*) printf '%s' "$1" ;;
    *)  printf '%s/%s' "$REPO_ROOT" "$1" ;;
  esac
}

begin_index_transaction() {
  local raw_index seed_temp
  raw_index="$(git rev-parse --git-path index)"
  INDEX_PATH="$(absolute_git_path "$raw_index")"
  INDEX_LOCK_PATH="$INDEX_PATH.lock"

  if ! (set -o noclobber; : > "$INDEX_LOCK_PATH") 2>/dev/null; then
    die "git index is already locked; retry after the other index writer finishes" 5
  fi
  INDEX_TRANSACTION_ACTIVE=1

  TEMP_INDEX_PATH="$(mktemp "$INDEX_PATH.agent-history.XXXXXX")" || \
    die "could not create alternate index beside the real index" 5
  if [ -f "$INDEX_PATH" ]; then
    cp -p "$INDEX_PATH" "$TEMP_INDEX_PATH" || die "could not copy the current index" 5
  else
    rm -f "$TEMP_INDEX_PATH"
    if git rev-parse --verify HEAD >/dev/null 2>&1; then
      seed_temp="$TEMP_INDEX_PATH"
      GIT_INDEX_FILE="$seed_temp" git read-tree HEAD || die "could not seed a missing index from HEAD" 5
    fi
  fi
  export GIT_INDEX_FILE="$TEMP_INDEX_PATH"
}

release_index_transaction() {
  unset GIT_INDEX_FILE
  [ -z "$TEMP_INDEX_PATH" ] || rm -f "$TEMP_INDEX_PATH"
  [ -z "$INDEX_LOCK_PATH" ] || rm -f "$INDEX_LOCK_PATH"
  TEMP_INDEX_PATH=""
  INDEX_LOCK_PATH=""
  INDEX_TRANSACTION_ACTIVE=0
}

commit_index_transaction() {
  [ -f "$TEMP_INDEX_PATH" ] || die "alternate index was not produced; real index unchanged" 5
  # Keep the canonical lock path present throughout both atomic renames. Other
  # normal Git writers cannot enter between validation and publication.
  mv -f "$TEMP_INDEX_PATH" "$INDEX_LOCK_PATH" || die "could not prepare the locked index update" 5
  TEMP_INDEX_PATH=""
  mv -f "$INDEX_LOCK_PATH" "$INDEX_PATH" || die "could not atomically publish the validated index" 5
  INDEX_LOCK_PATH=""
  INDEX_TRANSACTION_ACTIVE=0
  unset GIT_INDEX_FILE
}

reject_unmerged_candidates() {
  local path
  for path in "${CANDIDATES[@]}"; do
    if GIT_LITERAL_PATHSPECS=1 git ls-files -u -- "$path" | grep -q .; then
      die "unmerged artifact conflicts must be resolved explicitly; index unchanged" 5
    fi
  done
}

verify_selected_staged() {
  local path diff_rc
  reject_unmerged_candidates
  if ! has_staged_code_changes; then
    die "current commit index has no staged non-artifact feature diff; stage code explicitly before committing" 4
  fi
  for path in "${CANDIDATES[@]}"; do
    diff_rc=0
    GIT_LITERAL_PATHSPECS=1 git diff --cached --quiet -- "$path" || diff_rc=$?
    case "$diff_rc" in
      1) printf 'verified-staged: %s\n' "$path" ;;
      0) die "selected exact artifact has no staged diff in the current commit index; run exact staging first" 5 ;;
      *) die "could not verify selected artifact in the current commit index" 5 ;;
    esac
  done
  log "${#CANDIDATES[@]} exact artifact(s) verified in the current commit index."
}

# Validate the whole add set against the alternate index before its single
# mutating git process. The batch ignore check prevents partial staging, and
# --dry-run catches every other addability failure.
preflight_candidates() {
  local ignored_file check_rc=0
  ignored_file="$(mktemp "${TMPDIR:-/tmp}/agent-history-ignored.XXXXXX")"
  printf '%s\0' "${CANDIDATES[@]}" | git check-ignore -z --stdin > "$ignored_file" 2>/dev/null || check_rc=$?
  case "$check_rc" in
    0)
      rm -f "$ignored_file"
      die "selected artifact is ignored and cannot be staged safely (value not shown)" 5 ;;
    1) rm -f "$ignored_file" ;;
    *) rm -f "$ignored_file"; die "git check-ignore failed while validating the atomic add set" 5 ;;
  esac
  GIT_LITERAL_PATHSPECS=1 git add --dry-run -- "${CANDIDATES[@]}" >/dev/null 2>&1 || \
    die "one or more selected artifacts are not addable; index unchanged" 5
}

PRE_SANITIZE_MODES=()
PRE_SANITIZE_OIDS=()
SANITIZED_OIDS=()
PRE_SANITIZE_TREE=""
SANITIZED_TREE=""
SANITIZER_CHANGED=0

capture_pre_sanitize_entries() {
  local path record metadata entry_path mode oid stage extra old_ifs
  PRE_SANITIZE_MODES=()
  PRE_SANITIZE_OIDS=()
  for path in "${CANDIDATES[@]}"; do
    record="$(GIT_LITERAL_PATHSPECS=1 git -c core.quotePath=false ls-files --stage -- "$path" 2>/dev/null)" || \
      die "could not read an exact candidate entry; real index unchanged" 5
    [ -n "$record" ] || die "an exact candidate is absent from the alternate index; real index unchanged" 5
    case "$record" in *$'\n'*) die "an exact candidate did not resolve to one stage-0 entry; real index unchanged" 5 ;; esac
    case "$record" in *$'\t'*) ;;
      *) die "Git returned a malformed exact candidate entry; real index unchanged" 5 ;;
    esac
    metadata="${record%%$'\t'*}"
    entry_path="${record#*$'\t'}"
    [ "$entry_path" = "$path" ] || die "Git returned a mismatched exact candidate path; real index unchanged" 5
    old_ifs="$IFS"
    IFS=' '
    set -- $metadata
    IFS="$old_ifs"
    [ "$#" = "3" ] || die "Git returned malformed exact candidate metadata; real index unchanged" 5
    mode="$1"; oid="$2"; stage="$3"; extra="${4:-}"
    [ -z "$extra" ] || die "Git returned malformed exact candidate metadata; real index unchanged" 5
    case "$mode" in 100644|100755) ;;
      *) die "exact candidates must be regular index blobs; real index unchanged" 5 ;;
    esac
    [ "$stage" = "0" ] || die "exact candidates must be stage-0 entries; real index unchanged" 5
    case "${#oid}" in 40|64) ;;
      *) die "Git returned an invalid exact candidate object id; real index unchanged" 5 ;;
    esac
    case "$oid" in *[!0-9a-f]*) die "Git returned an invalid exact candidate object id; real index unchanged" 5 ;; esac
    PRE_SANITIZE_MODES+=("$mode")
    PRE_SANITIZE_OIDS+=("$oid")
  done
  PRE_SANITIZE_TREE="$(git write-tree 2>/dev/null)" || \
    die "could not snapshot the pre-sanitize candidate tree; real index unchanged" 5
}

verify_post_sanitize_entries() {
  local index path record metadata entry_path mode oid stage old_ifs
  index=0
  SANITIZED_OIDS=()
  for path in "${CANDIDATES[@]}"; do
    record="$(GIT_LITERAL_PATHSPECS=1 git -c core.quotePath=false ls-files --stage -- "$path" 2>/dev/null)" || \
      die "could not verify an exact sanitized entry; real index unchanged" 5
    [ -n "$record" ] || die "an exact sanitized entry is absent; real index unchanged" 5
    case "$record" in *$'\n'*) die "an exact sanitized entry is not stage 0; real index unchanged" 5 ;; esac
    case "$record" in *$'\t'*) ;;
      *) die "Git returned a malformed sanitized entry; real index unchanged" 5 ;;
    esac
    metadata="${record%%$'\t'*}"
    entry_path="${record#*$'\t'}"
    [ "$entry_path" = "$path" ] || die "Git returned a mismatched sanitized path; real index unchanged" 5
    old_ifs="$IFS"
    IFS=' '
    set -- $metadata
    IFS="$old_ifs"
    [ "$#" = "3" ] || die "Git returned malformed sanitized metadata; real index unchanged" 5
    mode="$1"; oid="$2"; stage="$3"
    [ "$stage" = "0" ] || die "an exact sanitized entry is not stage 0; real index unchanged" 5
    [ "$mode" = "${PRE_SANITIZE_MODES[$index]}" ] || \
      die "sanitation changed an exact candidate mode; real index unchanged" 5
    case "${#oid}" in 40|64) ;;
      *) die "Git returned an invalid sanitized object id; real index unchanged" 5 ;;
    esac
    case "$oid" in *[!0-9a-f]*) die "Git returned an invalid sanitized object id; real index unchanged" 5 ;; esac
    SANITIZED_OIDS+=("$oid")
    index=$((index + 1))
  done
}

run_redactor_index_mode() {
  local mode="$1" rc=0
  # A pinned policy must reach both passes. Sanitizing under the request's
  # policy but verifying under the worktree's would let a mid-run edit hide a
  # finding from the very check that gates the commit. Bash 3.2 expands an empty
  # array under `set -u` to one empty word, so branch instead of splicing.
  case "$mode" in
    fix)
      if [ "$GITLEAKS_CONFIG_SET" = "1" ]; then
        set -- "$PYTHON_BIN" "$REDACTOR" --config "$GITLEAKS_CONFIG" --fix-index \
          --paths "${ARTIFACT_DIRS[@]}" --files "${CANDIDATES[@]}"
      else
        set -- "$PYTHON_BIN" "$REDACTOR" --fix-index \
          --paths "${ARTIFACT_DIRS[@]}" --files "${CANDIDATES[@]}"
      fi ;;
    check)
      if [ "$GITLEAKS_CONFIG_SET" = "1" ]; then
        set -- "$PYTHON_BIN" "$REDACTOR" --config "$GITLEAKS_CONFIG" --check-index \
          --paths "${ARTIFACT_DIRS[@]}"
      else
        set -- "$PYTHON_BIN" "$REDACTOR" --check-index --paths "${ARTIFACT_DIRS[@]}"
      fi ;;
    *) return 2 ;;
  esac
  if [ -n "$RECEIPT_REQUEST" ]; then
    if [ "$mode" = fix ]; then
      set -- "$@" --receipt-request "$RECEIPT_REQUEST"
    else
      set -- "$@" --full-index
    fi
  fi
  "$@" >/dev/null 2>&1 || rc=$?
  return "$rc"
}

sanitize_candidate_index() {
  local sanitize_rc=0 check_rc=0
  capture_pre_sanitize_entries
  run_redactor_index_mode fix || sanitize_rc=$?
  [ "$sanitize_rc" = "0" ] || \
    die "exact artifact sanitation failed; scanner details were suppressed and the real index is unchanged" 5
  verify_post_sanitize_entries
  run_redactor_index_mode check || check_rc=$?
  [ "$check_rc" = "0" ] || \
    die "sanitized candidate index did not pass a clean verification; real index unchanged" 5
  SANITIZED_TREE="$(git write-tree 2>/dev/null)" || \
    die "could not snapshot the sanitized candidate tree; real index unchanged" 5
  if [ "$SANITIZED_TREE" != "$PRE_SANITIZE_TREE" ]; then
    SANITIZER_CHANGED=1
  fi
}

regular_file_identity() {
  "$PYTHON_BIN" - "$1" <<'PY'
import os
import stat
import sys

try:
    info = os.lstat(sys.argv[1])
except OSError:
    raise SystemExit(1)
if not stat.S_ISREG(info.st_mode):
    raise SystemExit(1)
print(f"{info.st_dev}:{info.st_ino}")
PY
}

owned_backup_action() {
  local action="$1" backup="$2" identity="$3" target="${4:-}"
  "$PYTHON_BIN" - "$action" "$backup" "$identity" "$target" <<'PY'
import os
import stat
import sys

action, backup, expected, target = sys.argv[1:]
try:
    info = os.lstat(backup)
except FileNotFoundError:
    raise SystemExit(0 if action == "remove" else 1)
except OSError:
    raise SystemExit(1)
identity = f"{info.st_dev}:{info.st_ino}"
if identity != expected or not stat.S_ISREG(info.st_mode):
    raise SystemExit(1)
if action == "check":
    raise SystemExit(0)
if action == "remove":
    try:
        os.unlink(backup)
    except OSError:
        raise SystemExit(1)
    raise SystemExit(0)
if action == "restore":
    if not target or os.path.dirname(backup) != os.path.dirname(target):
        raise SystemExit(1)
    try:
        os.replace(backup, target)
        restored = os.lstat(target)
    except OSError:
        raise SystemExit(1)
    if f"{restored.st_dev}:{restored.st_ino}" != expected:
        raise SystemExit(1)
    raise SystemExit(0)
raise SystemExit(1)
PY
}

same_owned_inode() {
  local path="$1" backup="$2" identity="$3"
  "$PYTHON_BIN" - "$path" "$backup" "$identity" <<'PY'
import os
import stat
import sys

path, backup, expected = sys.argv[1:]
try:
    current = os.lstat(path)
    saved = os.lstat(backup)
except OSError:
    raise SystemExit(1)
if not stat.S_ISREG(current.st_mode) or not stat.S_ISREG(saved.st_mode):
    raise SystemExit(1)
actual = f"{saved.st_dev}:{saved.st_ino}"
if actual != expected:
    raise SystemExit(1)
raise SystemExit(0 if (current.st_dev, current.st_ino) == (saved.st_dev, saved.st_ino) else 1)
PY
}

stable_clean_hash() {
  local source="$1" attribute_path="$2" expected_identity="${3:-}"
  local before after oid
  before="$(regular_file_identity "$source")" || return 1
  [ -z "$expected_identity" ] || [ "$before" = "$expected_identity" ] || return 1
  oid="$(git hash-object --path="$attribute_path" -- "$source" 2>/dev/null)" || return 1
  after="$(regular_file_identity "$source")" || return 1
  [ "$before" = "$after" ] || return 1
  case "${#oid}" in 40|64) ;;
    *) return 1 ;;
  esac
  case "$oid" in *[!0-9a-f]*) return 1 ;; esac
  printf '%s' "$oid"
}

create_materialization_backups() {
  local index path source_id slot slot_id linked_id
  MATERIALIZE_BACKUPS=()
  MATERIALIZE_BACKUP_IDS=()
  MATERIALIZE_BACKUP_ACTIVE=()
  index=0
  for path in "${CANDIDATES[@]}"; do
    source_id="$(regular_file_identity "$path")" || \
      die "a selected live artifact is no longer a nonsymlink regular file; the real index is unchanged" 5
    slot="$(mktemp "$(dirname "$path")/.agent-history-backup.XXXXXX")" || \
      die "could not reserve a same-directory materialization backup; the real index is unchanged" 5
    slot_id="$(regular_file_identity "$slot")" || \
      die "could not validate a materialization backup reservation; the real index is unchanged" 5
    MATERIALIZE_BACKUPS+=("$slot")
    MATERIALIZE_BACKUP_IDS+=("$slot_id")
    MATERIALIZE_BACKUP_ACTIVE+=("1")
    owned_backup_action remove "$slot" "$slot_id" || \
      die "could not release a materialization backup reservation; the real index is unchanged" 5
    MATERIALIZE_BACKUP_IDS[index]="$source_id"
    ln "$path" "$slot" >/dev/null 2>&1 || \
      die "could not hard-link a selected live artifact for lossless materialization; the real index is unchanged" 5
    linked_id="$(regular_file_identity "$slot")" || \
      die "a materialization backup was replaced unexpectedly; the real index is unchanged" 5
    if [ "$linked_id" != "$source_id" ] || \
       ! same_owned_inode "$path" "$slot" "$source_id"; then
      die "a selected live artifact changed while its backup was created; the real index is unchanged" 5
    fi
    index=$((index + 1))
  done
}

materialization_backups_remaining() {
  local active
  for active in "${MATERIALIZE_BACKUP_ACTIVE[@]:-}"; do
    [ "$active" = "1" ] && return 0
  done
  return 1
}

discard_materialization_backups() {
  local index backup identity
  index=0
  # Validate every name before removing any, so a replaced sibling is never
  # unlinked just because it reused an owned backup pathname.
  for backup in "${MATERIALIZE_BACKUPS[@]:-}"; do
    if [ "${MATERIALIZE_BACKUP_ACTIVE[index]:-0}" = "1" ]; then
      identity="${MATERIALIZE_BACKUP_IDS[index]}"
      owned_backup_action check "$backup" "$identity" || return 1
    fi
    index=$((index + 1))
  done
  index=0
  for backup in "${MATERIALIZE_BACKUPS[@]:-}"; do
    if [ "${MATERIALIZE_BACKUP_ACTIVE[index]:-0}" = "1" ]; then
      identity="${MATERIALIZE_BACKUP_IDS[index]}"
      owned_backup_action remove "$backup" "$identity" || return 1
      MATERIALIZE_BACKUP_ACTIVE[index]=0
    fi
    index=$((index + 1))
  done
  return 0
}

verify_materialization_sources() {
  local index path backup identity expected backup_before path_oid backup_after
  index=0
  for path in "${CANDIDATES[@]}"; do
    backup="${MATERIALIZE_BACKUPS[$index]}"
    identity="${MATERIALIZE_BACKUP_IDS[index]}"
    expected="${PRE_SANITIZE_OIDS[$index]}"
    same_owned_inode "$path" "$backup" "$identity" || return 1
    backup_before="$(stable_clean_hash "$backup" "$path" "$identity")" || return 1
    path_oid="$(stable_clean_hash "$path" "$path" "$identity")" || return 1
    backup_after="$(stable_clean_hash "$backup" "$path" "$identity")" || return 1
    [ "$backup_before" = "$expected" ] && [ "$path_oid" = "$expected" ] && \
      [ "$backup_after" = "$expected" ] || return 1
    index=$((index + 1))
  done
  return 0
}

preserve_replacement_generation() {
  local path="$1" source_id slot slot_id linked_id
  source_id="$(regular_file_identity "$path")" || return 1
  slot="$(mktemp "$(dirname "$path")/.agent-history-recovery.XXXXXX")" || return 1
  slot_id="$(regular_file_identity "$slot")" || return 1
  owned_backup_action remove "$slot" "$slot_id" || return 1
  if ! ln "$path" "$slot" >/dev/null 2>&1; then
    # The reserved file is already gone; never remove an unknown replacement.
    return 1
  fi
  linked_id="$(regular_file_identity "$slot")" || return 1
  if [ "$linked_id" != "$source_id" ] || \
     ! same_owned_inode "$path" "$slot" "$source_id"; then
    # The pathname no longer names the inode we linked; preserve it rather than
    # deleting a replacement that we do not own.
    return 1
  fi
  MATERIALIZE_RECOVERY_LEFT=1
  return 0
}

rollback_materialization() {
  local index path backup identity old_oid new_oid old_changed new_changed
  local rollback_failed=0
  index=0
  for path in "${CANDIDATES[@]}"; do
    if [ "${MATERIALIZE_BACKUP_ACTIVE[index]:-0}" != "1" ]; then
      index=$((index + 1))
      continue
    fi
    backup="${MATERIALIZE_BACKUPS[$index]}"
    identity="${MATERIALIZE_BACKUP_IDS[index]}"

    # checkout-index did not replace this pathname. Removing our extra link is
    # the only rollback needed, and leaves the live generation untouched.
    if same_owned_inode "$path" "$backup" "$identity"; then
      if owned_backup_action remove "$backup" "$identity"; then
        MATERIALIZE_BACKUP_ACTIVE[index]=0
      else
        rollback_failed=1
      fi
      index=$((index + 1))
      continue
    fi

    old_oid="$(stable_clean_hash "$backup" "$path" "$identity")" || old_oid=""
    new_oid="$(stable_clean_hash "$path" "$path")" || new_oid=""
    old_changed=1
    new_changed=1
    [ "$old_oid" = "${PRE_SANITIZE_OIDS[$index]}" ] && old_changed=0
    [ "$new_oid" = "${SANITIZED_OIDS[$index]}" ] && new_changed=0

    if [ "$new_changed" = "0" ]; then
      # The replacement is exactly ours. Atomically put the old inode back,
      # including any append made through a writer's pre-checkout descriptor.
      if owned_backup_action restore "$backup" "$identity" "$path"; then
        MATERIALIZE_BACKUP_ACTIVE[index]=0
      else
        rollback_failed=1
      fi
    elif [ "$old_changed" = "0" ]; then
      # A writer changed/replaced the new path. Preserve that path verbatim and
      # drop only our unchanged old-inode link.
      if owned_backup_action remove "$backup" "$identity"; then
        MATERIALIZE_BACKUP_ACTIVE[index]=0
      else
        rollback_failed=1
      fi
    else
      # Both generations changed. Preserve the replacement under an owned
      # sibling recovery name before restoring the changed old inode. If that
      # cannot be proven, leave both existing names untouched for inspection.
      if preserve_replacement_generation "$path" && \
         owned_backup_action restore "$backup" "$identity" "$path"; then
        MATERIALIZE_BACKUP_ACTIVE[index]=0
      else
        rollback_failed=1
        MATERIALIZE_RECOVERY_LEFT=1
      fi
    fi
    index=$((index + 1))
  done
  [ "$rollback_failed" = "0" ]
}

cleanup_materialization() {
  materialization_backups_remaining || return 0
  if [ "$MATERIALIZATION_STARTED" = "1" ]; then
    rollback_materialization >/dev/null 2>&1 || \
      warn "materialization cleanup preserved an unresolved concurrent generation for inspection"
  else
    discard_materialization_backups >/dev/null 2>&1 || \
      warn "an owned materialization backup could not be safely removed"
  fi
}

materialize_candidate_index() {
  local index path backup identity old_before old_after new_before new_after
  local after_checkout_tree checkout_rc=0 old_changed=0 new_changed=0 index_changed=0

  create_materialization_backups
  if ! verify_materialization_sources; then
    die "a selected live artifact changed during sanitation; nothing was materialized and the real index is unchanged" 5
  fi

  # Even a zero-byte sanitation pass reaches the generation proof above. There
  # is no checkout to perform, but publication still depends on this late check.
  if [ "$SANITIZER_CHANGED" = "0" ]; then
    discard_materialization_backups || \
      die "a materialization backup changed unexpectedly; the real index is unchanged" 5
    return 0
  fi

  MATERIALIZATION_STARTED=1
  GIT_LITERAL_PATHSPECS=1 git checkout-index --force -- "${CANDIDATES[@]}" \
    >/dev/null 2>&1 || checkout_rc=$?

  after_checkout_tree="$(git write-tree 2>/dev/null)" || index_changed=1
  [ "$after_checkout_tree" = "$SANITIZED_TREE" ] || index_changed=1

  index=0
  for path in "${CANDIDATES[@]}"; do
    backup="${MATERIALIZE_BACKUPS[$index]}"
    identity="${MATERIALIZE_BACKUP_IDS[index]}"
    old_before="$(stable_clean_hash "$backup" "$path" "$identity")" || old_before=""
    new_before="$(stable_clean_hash "$path" "$path")" || new_before=""
    old_after="$(stable_clean_hash "$backup" "$path" "$identity")" || old_after=""
    new_after="$(stable_clean_hash "$path" "$path")" || new_after=""
    if [ "$old_before" != "${PRE_SANITIZE_OIDS[$index]}" ] || \
       [ "$old_after" != "${PRE_SANITIZE_OIDS[$index]}" ]; then
      old_changed=1
    fi
    if [ "$new_before" != "${SANITIZED_OIDS[$index]}" ] || \
       [ "$new_after" != "${SANITIZED_OIDS[$index]}" ]; then
      new_changed=1
    fi
    index=$((index + 1))
  done

  if [ "$checkout_rc" != "0" ] || [ "$index_changed" = "1" ] || \
     [ "$old_changed" = "1" ] || [ "$new_changed" = "1" ]; then
    rollback_materialization || true
    materialization_backups_remaining || MATERIALIZATION_STARTED=0
    if [ "$MATERIALIZE_RECOVERY_LEFT" = "1" ]; then
      warn "concurrent materialization generations were preserved for manual inspection"
    fi
    if [ "$old_changed" = "1" ]; then
      die "a pre-materialization artifact inode changed concurrently; its generation was restored or preserved and the real index is unchanged" 5
    fi
    if [ "$new_changed" = "1" ]; then
      die "a materialized artifact path changed concurrently; writer data was preserved and the real index is unchanged" 5
    fi
    die "sanitized worktree materialization failed; paths were rolled back conservatively and the real index is unchanged" 5
  fi

  if ! discard_materialization_backups; then
    rollback_materialization || true
    materialization_backups_remaining || MATERIALIZATION_STARTED=0
    die "a materialization backup changed unexpectedly; the real index is unchanged" 5
  fi
  MATERIALIZATION_STARTED=0
}

trap 'cleanup_materialization; cleanup_index_transaction' EXIT
trap 'exit 130' HUP INT TERM

run_exact_selector() {
  local selector_output selector_rc=0
  set -- "$BASH" "$SCRIPT_DIR/find-session.sh" --quiet --format "$1"
  [ "$SESSION_ID_SET" = "1" ] && set -- "$@" --session-id "$SESSION_ID"
  [ "$SPECSTORY_PATH_SET" = "1" ] && set -- "$@" --specstory-path "$SPECSTORY_INPUT"
  selector_output="$("$@")" || selector_rc=$?
  if [ "$selector_rc" != "0" ]; then
    printf '%s\n' "$selector_output" >&2
    die "exact session selector failed (find-session exit $selector_rc); index unchanged" 5
  fi
  printf '%s' "$selector_output"
}

CANDIDATES=()

if [ "$SESSION_ONLY" = "1" ]; then
  if [ "$NO_SPECSTORY" = "1" ]; then
    # Prove that the explicitly named raw Claude session belongs to this checkout.
    selector_output="$(run_exact_selector claude)"
  else
    # Prove both rendered and raw artifacts describe the same checkout/session.
    selector_output="$(run_exact_selector both)"
    specstory_absolute="$(printf '%s\n' "$selector_output" | awk -F '\t' '$1=="specstory_path" {print $2; exit}')"
    [ -n "$specstory_absolute" ] || die "exact selector returned no SpecStory path; index unchanged" 5
    if ! specstory_relative="$(repo_relative_path "$specstory_absolute")"; then
      die "exact selector returned a transcript outside the git root: $specstory_absolute" 5
    fi
    append_unique_candidate "$specstory_relative"
  fi

  if [ "$PLAN_SET" = "1" ]; then
    plan_rc=0
    plan_absolute="$(canonical_existing_file "$PLAN_INPUT")" || plan_rc=$?
    case "$plan_rc" in
      0) ;;
      2) die "--plan must not be a symlink (value not shown); index unchanged" 5 ;;
      *) die "--plan does not exist as a regular file (value not shown); index unchanged" 5 ;;
    esac
    if ! plan_relative="$(repo_relative_path "$plan_absolute")"; then
      die "--plan is outside the git root (value not shown); index unchanged" 5
    fi
    is_exact_plan_path "$plan_relative" || die "--plan must be Markdown under a configured artifact directory other than .specstory/history (value not shown)" 5
    append_unique_candidate "$plan_relative"
  fi
else
  collect_broad_artifacts
fi

# Complete path, ignore, and addability validation before any index mutation.
for artifact in "${CANDIDATES[@]:-}"; do
  [ -n "$artifact" ] || continue
  validate_candidate_path "$artifact"
done

# Prove the queued staged tree is still the one being finalized.
assert_expected_index_tree() {
  local current
  [ "$EXPECT_INDEX_TREE_SET" = "1" ] || return 0
  current="$(git write-tree 2>/dev/null)" || \
    die "could not snapshot the current staged tree; real index unchanged" 5
  [ "$current" = "$EXPECT_INDEX_TREE" ] || \
    die "the staged tree changed since the request was queued; real index unchanged" 6
}

if [ "$CHECK_STAGED" = "1" ]; then
  assert_expected_index_tree
  verify_selected_staged
  exit 0
fi

if [ "${#CANDIDATES[@]}" -eq 0 ]; then
  if has_staged_code_changes; then
    log "No agent artifacts to stage; staged non-artifact code changes are present — nothing to do."
    exit 0
  fi
  log "Nothing to stage: no staged non-artifact code and no dirty artifacts."
  exit 3
fi

begin_index_transaction
reject_unmerged_candidates
preflight_candidates

# Inside the transaction the alternate index is a copy of the real one and its
# lock is held, so the tree cannot change between this check and the add below.
assert_expected_index_tree

if [ "$ALLOW_EMPTY" = "0" ] && ! has_staged_code_changes; then
  log "Refusing: artifacts are dirty but no non-artifact code changes are staged."
  log "         Unstaged working-tree code will not be part of the next commit."
  log "         Stage the feature diff first, or use --allow-empty only for an intentional artifact-only commit."
  exit 4
fi

if [ "$DRY_RUN" = "1" ]; then
  for artifact in "${CANDIDATES[@]}"; do
    printf '[dry-run] would git add: %s\n' "$artifact"
  done
  release_index_transaction
  log "${#CANDIDATES[@]} artifact(s) validated; index unchanged."
  exit 0
fi

# One git process adds the exact candidates to the alternate index while the
# real index lock is held. Sanitization, optional filtered materialization, and
# final publication never re-add a live path.
GIT_LITERAL_PATHSPECS=1 git add -- "${CANDIDATES[@]}"
if [ "$SANITIZE_INDEX" = "1" ]; then
  sanitize_candidate_index
  if [ "$MATERIALIZE_SANITIZED" = "1" ]; then
    materialize_candidate_index
  fi
fi
commit_index_transaction

if [ "$SANITIZER_CHANGED" = "1" ]; then
  log "Exact artifact content was sanitized atomically; credential rotation is required before commit."
  exit 10
fi
for artifact in "${CANDIDATES[@]}"; do
  printf 'staged: %s\n' "$artifact"
done
log "${#CANDIDATES[@]} artifact(s) staged atomically with the feature index."
