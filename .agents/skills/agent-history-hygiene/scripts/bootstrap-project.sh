#!/usr/bin/env bash
# bootstrap-project.sh — wire validation-only pre-commit checks + gitleaks
# into a project so agent chat/plan files can be committed safely. Sanctioned
# redaction runs only in the post-session finalizer after recording stops.
#
# Bash 3.2 compatible (stock macOS).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ASSETS_DIR="$SKILL_DIR/assets"

CHEZMOI_SRC="${CHEZMOI_SOURCE_DIR:-$HOME/.local/share/chezmoi}"

# The published hook is validation-only. Keep these values in lockstep with
# assets/pre-commit-config.yaml.template and the repo's .pre-commit-hooks.yaml.
HOOK_REPO_URL="https://github.com/daviddwlee84/agent-skills"
OLD_HOOK_REV="ahh-v1.1.0"
HOOK_REV="ahh-v2.0.1"
HOOK_ID="check-agent-artifact-secrets"
LEGACY_HOOK_ID="redact-agent-secrets"
# Derived from assets/artifact-dirs.txt after repository discovery. `.agents` is
# included separately because it is an install root rather than an artifact root.
ARCHIVE_EXCLUDE_REGEX=""
# Set when --migrate finds the config already on the exact current hook. That
# case must be a total no-op, so the run stops before any other bootstrap step.
MIGRATION_ALREADY_CURRENT=0
PRE_COMMIT_UVX_SPEC="pre-commit@4"

usage() {
  cat <<'EOF'
Usage: bootstrap-project.sh [OPTIONS]

Install agent-history-hygiene's validation stack into the current repo:
  .pre-commit-config.yaml   check-agent-artifact-secrets (pinned, read-only) +
                            gitleaks-system + standard hygiene
  .gitleaks.toml            (portable subset; real leaks still fire)
  .specstory/.gitignore     ignores only machine-local project identity +
                            generated statistics; keeps history committable
  .git/hooks/pre-commit     (via `pre-commit install`)

Pre-commit validates the effective staged index and never redacts or rewrites
files. The post-session finalizer is the sanctioned redaction path after the
transcript recorder has exited.

Options:
  --from-chezmoi      Symlink .pre-commit-config.yaml + .gitleaks.toml from your
                      chezmoi source ($HOME/.local/share/chezmoi) so updates
                      propagate. Fails if chezmoi source is missing.
  --migrate           Transactionally migrate the exact ahh-v1.1.0 remote hook
                      or the exact old repo: local vendored redactor layout to
                      check-agent-artifact-secrets@ahh-v2.0.1. Preserves sibling
                      hooks and only non-scoping compatible options; refuses
                      ambiguous commands and consumer scanner overrides. It does
                      not rewrite an existing gitleaks pin/config; merge the
                      v8.30.1 targeted allowlist separately.
  --install-hook      Install a validation-only prepare-commit-msg hook. Explicit
                      AGENT_HISTORY_* identity/plan must already have staged diffs
                      in the commit index; the hook never mutates any index.
                      Missing identity remains a visible no-op.
  --untrack-specstory-state
                      Stop tracking .specstory/.project.json and
                      .specstory/statistics.json after adding precise ignore
                      rules. Files remain on disk; stages their Git removals.
  --force             Overwrite existing files instead of skipping. Never makes
                      --migrate accept an unknown legacy layout.
  --dry-run           Build and validate the candidate without publishing it.
  --help, -h          Show this help and exit.

Exit codes:
  0  success or already on the exact new hook
  1  invalid arguments
  2  not inside a git repo
  3  chezmoi source missing (and --from-chezmoi was requested)
  4  no pre-commit validator is available
  5  migration refused or candidate validation failed (no migration writes)
  6  --install-hook requested while core.hooksPath redirects repo hooks
EOF
}

log()  { printf '%s\n' "$*" >&2; }
warn() { printf 'warn: %s\n' "$*" >&2; }
die()  { local message="$1" status="${2:-1}"; printf 'error: %s\n' "$message" >&2; exit "$status"; }

FROM_CHEZMOI=0
INSTALL_HOOK=0
FORCE=0
DRY_RUN=0
MIGRATE=0
UNTRACK_SPECSTORY_STATE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --from-chezmoi)  FROM_CHEZMOI=1; shift ;;
    --migrate)       MIGRATE=1; shift ;;
    --install-hook)  INSTALL_HOOK=1; shift ;;
    --untrack-specstory-state) UNTRACK_SPECSTORY_STATE=1; shift ;;
    --force)         FORCE=1; shift ;;
    --dry-run)       DRY_RUN=1; shift ;;
    --help|-h)       usage; exit 0 ;;
    -*)              die "unknown flag: $1 (try --help)" 1 ;;
    *)               die "unexpected positional arg: $1 (try --help)" 1 ;;
  esac
done

# Must be inside a git repo (or the pre-commit install step fails anyway).
if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
  die "not inside a git repo" 2
fi
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Generic mutators must exclude every configured archival/install root. Derive
# the root set from the same artifact manifest used by staging and scanning, then
# add `.agents` because installed skills are not themselves agent artifacts.
# Reject syntax outside a simple repo-root-relative hidden-directory path before
# interpolating it into a regular expression.
derive_archive_exclude_regex() {
  local dirs_file="$ASSETS_DIR/artifact-dirs.txt"
  local line root escaped alternatives="" old_ifs
  local roots=(".agents")

  [ -f "$dirs_file" ] || die "missing artifact directory manifest: $dirs_file" 1
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"
    line="$(printf '%s' "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [ -z "$line" ] && continue
    if [[ ! "$line" =~ ^\.[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$ ]]; then
      die "invalid artifact directory in $dirs_file: $line" 1
    fi
    root="${line%%/*}"
    roots+=("$root")
  done < "$dirs_file"

  old_ifs="$IFS"
  IFS=$'\n'
  roots=($(printf '%s\n' "${roots[@]}" | LC_ALL=C sort -u))
  IFS="$old_ifs"
  [ "${#roots[@]}" -gt 1 ] || die "artifact directory manifest has no usable roots: $dirs_file" 1

  for root in "${roots[@]}"; do
    # Roots are validated above; periods are the only regex metacharacters they
    # can contain. Escape them so the generated expression remains literal.
    escaped="${root//./\\.}"
    if [ -z "$alternatives" ]; then
      alternatives="$escaped"
    else
      alternatives="$alternatives|$escaped"
    fi
  done
  ARCHIVE_EXCLUDE_REGEX="^(?:${alternatives})(?:/|$)"
}
derive_archive_exclude_regex

# Track key presence separately from its value. Even `.git/hooks` is unsafe in
# linked worktrees because `.git` is a file there, so automatic repo-hook install
# is supported only when core.hooksPath is genuinely unset.
configured_hooks_path=""
HOOKS_PATH_CONFIGURED=0
if configured_hooks_path="$(git config --get core.hooksPath 2>/dev/null)"; then
  HOOKS_PATH_CONFIGURED=1
fi
HOOKS_REDIRECTED="$HOOKS_PATH_CONFIGURED"
HOOKS_PATH_INVALID=0
[ "$HOOKS_PATH_CONFIGURED" = "1" ] && [ -z "$configured_hooks_path" ] && HOOKS_PATH_INVALID=1

# Fail before writing bootstrap files so --install-hook cannot report success for
# a path that is root-relative, external, or non-traversable in a linked worktree.
if [ "$INSTALL_HOOK" = "1" ] && [ "$HOOKS_PATH_INVALID" = "1" ]; then
  die "--install-hook cannot use an explicitly empty core.hooksPath; unset the key first" 6
fi
if [ "$INSTALL_HOOK" = "1" ] && [ "$HOOKS_PATH_CONFIGURED" = "1" ]; then
  die "--install-hook requires core.hooksPath to be genuinely unset. Integrate the validation-only hook into the configured directory yourself, or unset the key." 6
fi

if [ "$FROM_CHEZMOI" = "1" ] && [ ! -d "$CHEZMOI_SRC" ]; then
  die "chezmoi source not found at $CHEZMOI_SRC (skip --from-chezmoi to use bundled copies)" 3
fi

dryrun_say() {
  [ "$DRY_RUN" = "1" ] && log "[dry-run] $*"
}

# install_file <dest> <src_copy> <src_chezmoi>
#
# Publish through a sibling temporary name rather than writing to `dest` in
# place. In particular, `cp source destination-symlink` follows an existing
# non-dangling symlink and can overwrite a file outside the project. rename(2)
# replaces the directory entry itself, so --force never follows either kind of
# destination symlink.
install_file() {
  local dest="$1" src_copy="$2" src_chezmoi="$3"
  local src dest_dir temp
  if [ "$FROM_CHEZMOI" = "1" ]; then
    src="$src_chezmoi"
    [ -e "$src" ] || die "missing upstream file: $src" 3
  else
    src="$src_copy"
    [ -e "$src" ] || die "missing bundled asset: $src" 1
  fi

  if { [ -e "$dest" ] || [ -L "$dest" ]; } && [ "$FORCE" != "1" ]; then
    log "skip: $dest already exists (use --force to overwrite)"
    return 0
  fi

  dest_dir="$(dirname "$dest")"
  mkdir -p "$dest_dir"

  if [ "$DRY_RUN" = "1" ]; then
    if [ "$FROM_CHEZMOI" = "1" ]; then
      log "[dry-run] atomically link $src -> $dest"
    else
      log "[dry-run] atomically copy $src -> $dest"
    fi
    return 0
  fi

  temp="$(mktemp "$dest_dir/.agent-history-install.XXXXXX")" || \
    die "could not create an installation candidate for $dest" 1
  if [ "$FROM_CHEZMOI" = "1" ]; then
    rm -f -- "$temp"
    if ! ln -s "$src" "$temp" || ! mv -f "$temp" "$dest"; then
      rm -f -- "$temp"
      die "could not install symlink: $dest" 1
    fi
    log "linked: $dest -> $src"
  else
    if ! cp "$src" "$temp" || ! mv -f "$temp" "$dest"; then
      rm -f -- "$temp"
      die "could not install file: $dest" 1
    fi
    log "wrote: $dest"
  fi
}

# --migrate is intentionally a narrow textual migration rather than a generic
# YAML reserializer. It preserves untouched bytes/comments, rejects unfamiliar
# redactor commands, validates a same-directory candidate, and only then uses an
# atomic rename. The legacy script is considered separately after publication.
MIGRATION_CANDIDATE=""
MIGRATION_META=""
cleanup_migration_temps() {
  [ -z "$MIGRATION_CANDIDATE" ] || rm -f -- "$MIGRATION_CANDIDATE"
  [ -z "$MIGRATION_META" ] || rm -f -- "$MIGRATION_META"
}
trap cleanup_migration_temps EXIT

validate_config_candidate() {
  local candidate="$1"
  if command -v pre-commit >/dev/null 2>&1; then
    pre-commit validate-config "$candidate"
    return $?
  fi
  if command -v uvx >/dev/null 2>&1; then
    log "pre-commit not found — validating with uvx $PRE_COMMIT_UVX_SPEC"
    uvx "$PRE_COMMIT_UVX_SPEC" validate-config "$candidate"
    return $?
  fi
  return 125
}

maybe_remove_legacy_redactor() {
  local migration_kind="$1" script_referenced="$2"
  local path="scripts/redact_secrets.py"
  local stage_info mode="" listed_oid="" stage="" tracked_path="" extra=""
  local index_oid head_oid worktree_oid

  [ "$migration_kind" = "local" ] || return 0
  [ -e "$path" ] || [ -L "$path" ] || return 0

  if [ "$script_referenced" != "0" ]; then
    warn "preserving $path: the migrated candidate still references it"
    return 0
  fi
  if [ -L "$path" ] || [ ! -f "$path" ]; then
    warn "preserving $path: exact legacy-content verification requires a regular file"
    return 0
  fi

  stage_info="$(git ls-files --stage -- "$path")"
  IFS=$' \t' read -r mode listed_oid stage tracked_path extra <<EOF || true
$stage_info
EOF
  if [ -n "$extra" ] || { [ "$mode" != "100644" ] && [ "$mode" != "100755" ]; } || \
     [ -z "$listed_oid" ] || [ "$stage" != "0" ] || [ "$tracked_path" != "$path" ]; then
    warn "preserving $path: it is not one exact stage-0 tracked regular file"
    return 0
  fi

  index_oid="$(git rev-parse --verify ":$path" 2>/dev/null || true)"
  head_oid="$(git rev-parse --verify "HEAD:$path" 2>/dev/null || true)"
  worktree_oid="$(git hash-object --no-filters -- "$path" 2>/dev/null || true)"
  if [ -z "$index_oid" ] || [ "$index_oid" != "$listed_oid" ] || \
     [ "$index_oid" != "$head_oid" ] || [ "$index_oid" != "$worktree_oid" ] || \
     ! git diff --quiet --no-ext-diff -- "$path" || \
     ! git diff --cached --quiet --no-ext-diff -- "$path"; then
    warn "preserving $path: exact legacy content is not provably unchanged from HEAD/index"
    return 0
  fi

  if [ "$DRY_RUN" = "1" ]; then
    log "[dry-run] remove verified unmodified legacy $path after publishing the candidate"
    return 0
  fi
  if rm -f -- "$path"; then
    log "removed: $path (verified unmodified legacy copy; deletion is unstaged)"
  else
    warn "could not remove $path after migration; the validated config is already published"
  fi
}

migrate_config() {
  local cfg=".pre-commit-config.yaml"
  local rc=0 migration_kind="" source_sha="" source_mode="" source_dev="" source_ino="" candidate_sha="" candidate_dev="" candidate_ino="" script_referenced=""

  [ -f "$cfg" ] || die "no $cfg to migrate (run without --migrate to create one)" 5
  [ ! -L "$cfg" ] || die "migration refuses a symlinked $cfg; update its source explicitly" 5
  command -v python3 >/dev/null 2>&1 || die "python3 is required for safe migration" 5

  MIGRATION_CANDIDATE="$(mktemp "$REPO_ROOT/.pre-commit-config.yaml.agent-history-hygiene.XXXXXX")" || \
    die "could not create a same-directory migration candidate" 5
  MIGRATION_META="$(mktemp "$REPO_ROOT/.pre-commit-config.yaml.agent-history-hygiene-meta.XXXXXX")" || \
    die "could not create migration metadata" 5

  python3 - "$cfg" "$MIGRATION_CANDIDATE" "$MIGRATION_META" \
    "$HOOK_REPO_URL" "$OLD_HOOK_REV" "$HOOK_REV" \
    "$LEGACY_HOOK_ID" "$HOOK_ID" "$ARCHIVE_EXCLUDE_REGEX" <<'PY' || rc=$?
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

(
    cfg_arg,
    candidate_arg,
    meta_arg,
    hook_url,
    old_rev,
    new_rev,
    old_id,
    new_id,
    archive_exclude,
) = sys.argv[1:]
cfg = Path(cfg_arg)
candidate = Path(candidate_arg)
meta = Path(meta_arg)


class Refusal(RuntimeError):
    pass


def trivia(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def trim_trivia(lines: list[str], start: int, end: int) -> int:
    while end > start and trivia(lines[end - 1]):
        end -= 1
    return end


def newline_of(lines: list[str]) -> str:
    for line in lines:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith("\n"):
            return "\n"
    return "\n"


def split_inline_comment(raw: str) -> tuple[str, str]:
    single = False
    double = False
    escaped = False
    for index, char in enumerate(raw):
        if double and escaped:
            escaped = False
            continue
        if double and char == "\\":
            escaped = True
            continue
        if not double and char == "'":
            single = not single
            continue
        if not single and char == '"':
            double = not double
            continue
        if not single and not double and char == "#" and (
            index == 0 or raw[index - 1].isspace()
        ):
            cut = index
            while cut > 0 and raw[cut - 1] in " \t":
                cut -= 1
            return raw[:cut], raw[cut:]
    cut = len(raw)
    while cut > 0 and raw[cut - 1] in " \t":
        cut -= 1
    return raw[:cut], raw[cut:]


def decode_scalar(raw: str, what: str) -> str:
    value = raw.strip()
    if not value or value[0] in "|>" or value.startswith(("&", "*")):
        raise Refusal(f"{what} must be one explicit single-line scalar")
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise Refusal(f"{what} has unsupported quoting")
        return value[1:-1].replace("''", "'")
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise Refusal(f"{what} has unsupported quoting") from exc
        if not isinstance(decoded, str):
            raise Refusal(f"{what} must be a string")
        return decoded
    if value.endswith(("'", '"')):
        raise Refusal(f"{what} has unsupported quoting")
    return value


def scalar_from_match(match: re.Match[str], what: str) -> str:
    value, _suffix = split_inline_comment(match.group(2))
    return decode_scalar(value, what)


def replace_match_scalar(match: re.Match[str], value: str) -> str:
    _old, suffix = split_inline_comment(match.group(2))
    return f"{match.group(1)}{value}{suffix}{match.group(3) or ''}"


def active_line(line: str) -> str:
    body = line[:-2] if line.endswith("\r\n") else line[:-1] if line.endswith("\n") else line
    value, _suffix = split_inline_comment(body)
    return value


REPO_RE = re.compile(r"^(  - repo:[ \t]*)(.*?)(\r?\n)?$")
REV_RE = re.compile(r"^(    rev:[ \t]*)(.*?)(\r?\n)?$")
HOOKS_RE = re.compile(r"^    hooks:[ \t]*(?:#.*)?(?:\r?\n)?$")
HOOKS_KEY_RE = re.compile(r"^    hooks[ \t]*:")
HOOK_ID_RE = re.compile(r"^(      - id:[ \t]*)(.*?)(\r?\n)?$")
OPTION_RE = re.compile(r"^(        )([A-Za-z][A-Za-z0-9_-]*):[ \t]*(.*?)(\r?\n)?$")
TOP_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*:")


def option_match(line: str):
    return OPTION_RE.match(line)


def option_scalar(option: dict, lines: list[str], what: str) -> str:
    match = option_match(lines[option["index"]])
    if match is None:
        raise Refusal(f"could not parse {what}")
    for line in lines[option["index"] + 1 : option["end"]]:
        if not trivia(line):
            raise Refusal(f"{what} must be a single-line scalar")
    value, _suffix = split_inline_comment(match.group(3))
    return decode_scalar(value, what)


def parse_options(hook: dict, lines: list[str]) -> list[dict]:
    direct: list[tuple[int, str]] = []
    for index in range(hook["start"] + 1, hook["end"]):
        match = option_match(lines[index])
        if match:
            direct.append((index, match.group(2)))

    first = direct[0][0] if direct else hook["end"]
    if any(not trivia(line) for line in lines[hook["start"] + 1 : first]):
        raise Refusal(f"hook {hook['id']} has unsupported indentation or flow syntax")

    starts: list[int] = []
    floor = hook["start"] + 1
    for index, _key in direct:
        chunk_start = index
        while chunk_start > floor and trivia(lines[chunk_start - 1]):
            chunk_start -= 1
        starts.append(chunk_start)
        floor = index + 1

    options: list[dict] = []
    seen: set[str] = set()
    for position, (index, key) in enumerate(direct):
        if key in seen:
            raise Refusal(f"hook {hook['id']} repeats option {key}")
        seen.add(key)
        options.append(
            {
                "key": key,
                "index": index,
                "start": starts[position],
                "end": starts[position + 1] if position + 1 < len(starts) else hook["end"],
            }
        )
    return options


def parse_structure(lines: list[str]) -> tuple[list[dict], list[dict]]:
    repos_headers = []
    for index, line in enumerate(lines):
        if re.match(r"^repos:[ \t]*(?:#.*)?(?:\r?\n)?$", line):
            repos_headers.append(index)
    if len(repos_headers) != 1:
        raise Refusal("config must contain one block-style top-level repos: list")
    header = repos_headers[0]
    repos_end = len(lines)
    for index in range(header + 1, len(lines)):
        body = active_line(lines[index])
        if body and not body[0].isspace() and TOP_KEY_RE.match(body):
            repos_end = index
            break

    starts = [
        index
        for index in range(header + 1, repos_end)
        if REPO_RE.match(lines[index])
    ]
    repos: list[dict] = []
    hooks: list[dict] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else repos_end
        content_end = trim_trivia(lines, start + 1, end)
        repo_match = REPO_RE.match(lines[start])
        assert repo_match is not None
        value = scalar_from_match(repo_match, "repo")
        rev_indexes = [
            index for index in range(start + 1, content_end) if REV_RE.match(lines[index])
        ]
        hooks_indexes = [
            index for index in range(start + 1, content_end) if HOOKS_RE.match(lines[index])
        ]
        repo = {
            "start": start,
            "end": end,
            "content_end": content_end,
            "value": value,
            "rev_indexes": rev_indexes,
            "hooks_indexes": hooks_indexes,
            "hooks": [],
        }
        repos.append(repo)
        if len(hooks_indexes) != 1:
            continue
        hooks_index = hooks_indexes[0]
        hook_starts = [
            index
            for index in range(hooks_index + 1, content_end)
            if HOOK_ID_RE.match(lines[index])
        ]
        for hook_position, hook_start in enumerate(hook_starts):
            hook_end = (
                hook_starts[hook_position + 1]
                if hook_position + 1 < len(hook_starts)
                else content_end
            )
            hook_end = trim_trivia(lines, hook_start + 1, hook_end)
            id_match = HOOK_ID_RE.match(lines[hook_start])
            assert id_match is not None
            hook = {
                "start": hook_start,
                "end": hook_end,
                "id": scalar_from_match(id_match, "hook id"),
                "repo": repo,
            }
            repo["hooks"].append(hook)
            hooks.append(hook)
    return repos, hooks


def validate_target_hook_structure(repo: dict, lines: list[str], what: str) -> None:
    hook_key_indexes = [
        index
        for index in range(repo["start"] + 1, repo["content_end"])
        if HOOKS_KEY_RE.match(active_line(lines[index]))
    ]
    if len(hook_key_indexes) != 1 or hook_key_indexes != repo["hooks_indexes"]:
        raise Refusal(f"{what} contains an unsupported hook item or flow-style hooks structure")

    hooks_index = hook_key_indexes[0]
    recognized_starts = {hook["start"] for hook in repo["hooks"]}
    for index in range(hooks_index + 1, repo["content_end"]):
        body = active_line(lines[index])
        if not body.strip():
            continue
        indentation = len(body) - len(body.lstrip(" "))
        if indentation < 6 or (indentation == 6 and index not in recognized_starts):
            raise Refusal(
                f"{what} contains an unsupported hook item or flow-style hooks structure"
            )


def scalar_marker_count(lines: list[str], token: str) -> int:
    pattern = re.compile(
        r"(?:\bid|['\"]id['\"])[ \t]*:[ \t]*['\"]?"
        + re.escape(token)
        + r"(?:['\"]|[,}\]]|\s|$)"
    )
    return sum(bool(pattern.search(active_line(line))) for line in lines)


def direct_repo_keys(repo: dict, lines: list[str]) -> set[str]:
    keys: set[str] = set()
    for line in lines[repo["start"] + 1 : repo["content_end"]]:
        match = re.match(r"^    ([A-Za-z][A-Za-z0-9_-]*):", line)
        if match:
            keys.add(match.group(1))
    return keys


def repo_revision(repo: dict, lines: list[str]) -> str:
    if len(repo["rev_indexes"]) != 1:
        raise Refusal("remote redactor repo must contain exactly one rev")
    match = REV_RE.match(lines[repo["rev_indexes"][0]])
    assert match is not None
    return scalar_from_match(match, "rev")


def hook_option_map(hook: dict, lines: list[str]) -> tuple[list[dict], dict[str, dict]]:
    options = parse_options(hook, lines)
    return options, {option["key"]: option for option in options}


# The exported hook owns invocation and staged-index scope: it is always run,
# receives no filenames, has no type filters, and discovers artifacts itself.
# A migration must not carry across consumer settings that could restore a
# pre-commit trigger gate or alter the checker command.
SCANNER_OVERRIDE_KEYS = {
    "always_run",
    "entry",
    "exclude_types",
    "language",
    "pass_filenames",
    "types",
    "types_or",
}
# Preserved verbatim, comments included. `stages` can opt into another hook
# stage; `files`/`exclude` are consumer trigger controls that the exported hook
# renders inert (it declares `always_run: true` + `pass_filenames: false`, so
# pre-commit consults neither before running it). Carrying them across keeps the
# consumer's documented intent without narrowing the staged-index scan. Anything
# else -- `args` included -- is refused as an unsupported option below.
COMPATIBLE_MIGRATION_KEYS = {"stages", "files", "exclude"}


def reject_scanner_overrides(by_key: dict[str, dict], what: str) -> None:
    unsafe = set(by_key) & SCANNER_OVERRIDE_KEYS
    if unsafe:
        raise Refusal(
            f"{what} has unsafe checker/scope overrides: "
            + ", ".join(sorted(unsafe))
        )
    unsupported = set(by_key) - COMPATIBLE_MIGRATION_KEYS
    if unsupported:
        raise Refusal(
            f"{what} has unsupported options: " + ", ".join(sorted(unsupported))
        )


def replace_id_line(line: str) -> str:
    match = HOOK_ID_RE.match(line)
    assert match is not None
    return replace_match_scalar(match, new_id)


def terminated(block: list[str], newline: str) -> list[str]:
    if block and not block[-1].endswith(("\n", "\r\n")):
        block = block[:-1] + [block[-1] + newline]
    return block


def new_hook_lines(hook: dict, lines: list[str], safe_options: list[dict], newline: str) -> list[str]:
    result = [replace_id_line(lines[hook["start"]])]
    for option in safe_options:
        result.extend(lines[option["start"] : option["end"]])
    return terminated(result, newline)


def apply_replacements(lines: list[str], replacements: list[tuple[int, int, list[str]]]) -> list[str]:
    result = list(lines)
    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        result[start:end] = replacement
    return result


def migrate_redactor(lines: list[str], newline: str) -> tuple[list[str], str]:
    repos, hooks = parse_structure(lines)
    old_hooks = [hook for hook in hooks if hook["id"] == old_id]
    new_hooks = [hook for hook in hooks if hook["id"] == new_id]

    if scalar_marker_count(lines, old_id) != len(old_hooks) or \
       scalar_marker_count(lines, new_id) != len(new_hooks):
        raise Refusal("redactor hook uses unsupported indentation or flow syntax")
    if len(old_hooks) + len(new_hooks) > 1:
        raise Refusal("multiple old/new agent-artifact secret hooks are ambiguous")
    if new_hooks:
        hook = new_hooks[0]
        repo = hook["repo"]
        validate_target_hook_structure(repo, lines, "target redactor repo")
        if repo["value"] != hook_url or repo_revision(repo, lines) != new_rev:
            raise Refusal("the new hook id is not on the exact approved repo/revision")
        _options, by_key = hook_option_map(hook, lines)
        reject_scanner_overrides(by_key, "new validation hook")
        return lines, "already-new"
    if not old_hooks:
        active = "\n".join(active_line(line) for line in lines)
        if old_id in active or new_id in active or "redact_secrets.py" in active:
            raise Refusal("found an unrecognized redactor entry")
        raise Refusal("no exact legacy redactor hook was found")

    hook = old_hooks[0]
    repo = hook["repo"]
    validate_target_hook_structure(repo, lines, "target redactor repo")
    options, by_key = hook_option_map(hook, lines)
    safe_keys = COMPATIBLE_MIGRATION_KEYS
    migration_kind: str

    script_ref_indexes = [
        index for index, line in enumerate(lines) if "redact_secrets.py" in active_line(line)
    ]
    if repo["value"] == hook_url:
        migration_kind = "remote"
        if repo_revision(repo, lines) != old_rev:
            raise Refusal(f"remote legacy hook must be pinned exactly to {old_rev}")
        if direct_repo_keys(repo, lines) - {"rev", "hooks"}:
            raise Refusal("remote legacy repo contains unsupported repo options")
        reject_scanner_overrides(by_key, "remote legacy hook")
        if script_ref_indexes:
            raise Refusal("a remote legacy hook plus a vendored-script entry is ambiguous")
    elif repo["value"] == "local":
        migration_kind = "local"
        required = {"name", "entry", "language", "pass_filenames"}
        additional = set(by_key) - required
        unsafe = additional & SCANNER_OVERRIDE_KEYS
        missing = required - set(by_key)
        if unsafe:
            raise Refusal(
                "local legacy hook has unsafe checker/scope overrides: "
                + ", ".join(sorted(unsafe))
            )
        unsupported = additional - safe_keys
        if unsupported:
            raise Refusal(
                "local legacy hook has unsupported options: " + ", ".join(sorted(unsupported))
            )
        if missing:
            raise Refusal(
                "local legacy hook is missing exact fields: " + ", ".join(sorted(missing))
            )
        expected = {
            "name": "Auto-redact secrets in agent artifacts",
            "entry": "./scripts/redact_secrets.py --fix",
            "language": "system",
            "pass_filenames": "false",
        }
        for key, value in expected.items():
            if option_scalar(by_key[key], lines, f"local redactor {key}") != value:
                raise Refusal(f"local redactor {key} is customized; refusing migration")
        entry_index = by_key["entry"]["index"]
        if script_ref_indexes != [entry_index]:
            raise Refusal("multiple or unknown vendored-script entries are ambiguous")
        if repo["rev_indexes"] or direct_repo_keys(repo, lines) - {"hooks"}:
            raise Refusal("local legacy repo contains unsupported repo options")
    else:
        raise Refusal("legacy hook is not in the exact approved remote or repo: local layout")

    safe_options = [option for option in options if option["key"] in safe_keys]
    replacement_hook = new_hook_lines(hook, lines, safe_options, newline)
    siblings = [candidate_hook for candidate_hook in repo["hooks"] if candidate_hook is not hook]
    replacements: list[tuple[int, int, list[str]]] = []

    if not siblings:
        replacements.append((hook["start"], hook["end"], replacement_hook))
        if migration_kind == "remote":
            rev_index = repo["rev_indexes"][0]
            rev_match = REV_RE.match(lines[rev_index])
            assert rev_match is not None
            replacements.append(
                (rev_index, rev_index + 1, [replace_match_scalar(rev_match, new_rev)])
            )
        else:
            repo_match = REPO_RE.match(lines[repo["start"]])
            assert repo_match is not None
            repo_line = replace_match_scalar(repo_match, hook_url)
            if not repo_line.endswith(("\n", "\r\n")):
                repo_line += newline
            replacements.append(
                (
                    repo["start"],
                    repo["start"] + 1,
                    [repo_line, f"    rev: {new_rev}{newline}"],
                )
            )
    else:
        replacements.append((hook["start"], hook["end"], []))
        remote_block = [
            newline,
            f"  - repo: {hook_url}{newline}",
            f"    rev: {new_rev}{newline}",
            f"    hooks:{newline}",
        ] + replacement_hook + [newline]
        replacements.append((repo["content_end"], repo["content_end"], remote_block))

    return apply_replacements(lines, replacements), migration_kind


def quote_single(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def rewrite_exclude_line(line: str, value: str) -> str:
    match = option_match(line)
    assert match is not None and match.group(2) == "exclude"
    _old, suffix = split_inline_comment(match.group(3))
    return f"{match.group(1)}exclude: {quote_single(value)}{suffix}{match.group(4) or ''}"


def merge_mutator_excludes(lines: list[str], newline: str) -> list[str]:
    repos, hooks = parse_structure(lines)
    replacements: list[tuple[int, int, list[str]]] = []
    mutator_repo = "https://github.com/pre-commit/pre-commit-hooks"
    mutator_ids = {"end-of-file-fixer", "trailing-whitespace"}
    old_specstory = {r"^\.specstory/", r"^\.specstory/.*", r"^\.specstory(?:/|$)"}

    for repo in repos:
        if repo["value"] != mutator_repo:
            continue
        repo_hooks = [hook for hook in repo["hooks"] if hook["id"] in mutator_ids]
        repo_lines = lines[repo["start"] : repo["content_end"]]
        for hook_id in mutator_ids:
            parsed_count = sum(hook["id"] == hook_id for hook in repo_hooks)
            if scalar_marker_count(repo_lines, hook_id) != parsed_count:
                raise Refusal(
                    f"{hook_id} uses unsupported indentation or flow syntax in its target repo"
                )
        if repo_hooks:
            validate_target_hook_structure(repo, lines, "target mutator repo")

    for hook in hooks:
        if hook["repo"]["value"] != mutator_repo or hook["id"] not in mutator_ids:
            continue
        options, by_key = hook_option_map(hook, lines)
        exclude_options = [option for option in options if option["key"] == "exclude"]
        if not exclude_options:
            addition: list[str] = []
            if hook["end"] > hook["start"] and not lines[hook["end"] - 1].endswith(("\n", "\r\n")):
                addition.append(newline)
            addition.append(f"        exclude: {quote_single(archive_exclude)}{newline}")
            replacements.append((hook["end"], hook["end"], addition))
            continue

        option = exclude_options[0]
        current = option_scalar(option, lines, f"{hook['id']} exclude")
        if archive_exclude in current:
            continue
        if re.match(r"^\(\?[aiLmsux-]+[):]", current):
            raise Refusal(f"{hook['id']} uses a flagged exclude regex that cannot be merged safely")
        merged = archive_exclude if current in old_specstory else f"(?:{current})|(?:{archive_exclude})"
        replacements.append(
            (
                option["index"],
                option["index"] + 1,
                [rewrite_exclude_line(lines[option["index"]], merged)],
            )
        )
    return apply_replacements(lines, replacements)


def candidate_references_script(lines: list[str]) -> bool:
    return any("scripts/redact_secrets.py" in active_line(line) for line in lines)


def same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def lstat_owned_regular(path: Path, what: str, *, single_link: bool = False) -> os.stat_result:
    result = os.lstat(path)
    if not stat.S_ISREG(result.st_mode):
        raise Refusal(f"{what} must be a regular file")
    if result.st_uid != os.geteuid():
        raise Refusal(f"{what} must be owned by the invoking user")
    if single_link and result.st_nlink != 1:
        raise Refusal(f"{what} must be an unlinked temporary inode")
    return result


def checked_open(
    path: Path,
    expected: os.stat_result,
    flags: int,
    what: str,
    *,
    single_link: bool = False,
) -> tuple[int, os.stat_result]:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise OSError("O_NOFOLLOW is unavailable")
    descriptor = os.open(os.fspath(path), flags | nofollow)
    try:
        actual = os.fstat(descriptor)
        if not stat.S_ISREG(actual.st_mode) or actual.st_uid != os.geteuid():
            raise Refusal(f"{what} changed to an unsafe inode")
        if single_link and actual.st_nlink != 1:
            raise Refusal(f"{what} changed to a linked inode")
        if not same_inode(expected, actual):
            raise Refusal(f"{what} path was swapped")
        named = os.lstat(path)
        if not same_inode(actual, named):
            raise Refusal(f"{what} path was swapped")
        return descriptor, actual
    except BaseException:
        os.close(descriptor)
        raise


def read_checked(path: Path, expected: os.stat_result, what: str) -> tuple[bytes, os.stat_result]:
    descriptor, actual = checked_open(path, expected, os.O_RDONLY, what)
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            return handle.read(), actual
    except BaseException:
        # fdopen closes the descriptor after construction; only close here when
        # construction itself failed.
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def assert_path_identity(
    path: Path,
    expected: os.stat_result,
    what: str,
    *,
    single_link: bool = False,
) -> None:
    current = lstat_owned_regular(path, what, single_link=single_link)
    if not same_inode(expected, current):
        raise Refusal(f"{what} path was swapped")


def write_owned_candidate(path: Path, data: bytes, mode: int, what: str) -> os.stat_result:
    expected = lstat_owned_regular(path, what, single_link=True)
    descriptor, actual = checked_open(
        path,
        expected,
        os.O_WRONLY,
        what,
        single_link=True,
    )
    try:
        # Do not request O_TRUNC before proving we opened the mktemp-created,
        # owned inode. A pathname swap must fail without truncating its target.
        os.ftruncate(descriptor, 0)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short candidate write")
            view = view[written:]
        os.fchmod(descriptor, stat.S_IMODE(mode))
        os.fsync(descriptor)
        assert_path_identity(path, actual, what, single_link=True)
        return actual
    finally:
        os.close(descriptor)


try:
    initial_source_stat = lstat_owned_regular(cfg, "config")
    source, source_stat = read_checked(cfg, initial_source_stat, "config")
    source_mode = stat.S_IMODE(source_stat.st_mode)
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Refusal("config must be UTF-8") from exc
    lines = text.splitlines(keepends=True)
    newline = newline_of(lines)
    migrated, kind = migrate_redactor(lines, newline)
    migrated = merge_mutator_excludes(migrated, newline)
    candidate_bytes = "".join(migrated).encode("utf-8")
    candidate_stat = write_owned_candidate(candidate, candidate_bytes, source_mode, "candidate")
    metadata = (
        f"kind={kind}\n"
        f"source_sha={hashlib.sha256(source).hexdigest()}\n"
        f"source_mode={source_mode:04o}\n"
        f"source_dev={source_stat.st_dev}\n"
        f"source_ino={source_stat.st_ino}\n"
        f"candidate_sha={hashlib.sha256(candidate_bytes).hexdigest()}\n"
        f"candidate_dev={candidate_stat.st_dev}\n"
        f"candidate_ino={candidate_stat.st_ino}\n"
        f"script_referenced={int(candidate_references_script(migrated))}\n"
    ).encode("ascii")
    write_owned_candidate(meta, metadata, 0o600, "candidate metadata")
except Refusal as exc:
    print(f"migration refused: {exc}", file=sys.stderr)
    sys.exit(5)
except OSError:
    print("migration refused: filesystem operation failed while building candidate", file=sys.stderr)
    sys.exit(5)
PY
  if [ "$rc" != "0" ]; then
    [ "$rc" = "5" ] || warn "candidate construction failed with status $rc"
    exit 5
  fi

  while IFS='=' read -r key value; do
    case "$key" in
      kind) migration_kind="$value" ;;
      source_sha) source_sha="$value" ;;
      source_mode) source_mode="$value" ;;
      source_dev) source_dev="$value" ;;
      source_ino) source_ino="$value" ;;
      candidate_sha) candidate_sha="$value" ;;
      candidate_dev) candidate_dev="$value" ;;
      candidate_ino) candidate_ino="$value" ;;
      script_referenced) script_referenced="$value" ;;
      *) die "migration metadata was invalid" 5 ;;
    esac
  done < "$MIGRATION_META"
  [ -n "$migration_kind" ] && [ -n "$source_sha" ] && \
    [ -n "$source_mode" ] && [ -n "$source_dev" ] && [ -n "$source_ino" ] && \
    [ -n "$candidate_sha" ] && [ -n "$candidate_dev" ] && [ -n "$candidate_ino" ] && \
    [ -n "$script_referenced" ] || die "migration metadata was incomplete" 5

  if [ "$migration_kind" = "already-new" ] && [ "$source_sha" = "$candidate_sha" ]; then
    log "$cfg already uses $HOOK_ID@$HOOK_REV with current mutator exclusions; leaving its config unchanged"
    MIGRATION_ALREADY_CURRENT=1
    return 0
  fi

  rc=0
  validate_config_candidate "$MIGRATION_CANDIDATE" || rc=$?
  if [ "$rc" != "0" ]; then
    if [ "$rc" = "125" ]; then
      die "no pre-commit validator available; candidate was not published" 4
    fi
    die "pre-commit rejected the complete migration candidate; no migration files changed" 5
  fi

  EXPECTED_SOURCE_SHA="$source_sha" EXPECTED_SOURCE_MODE="$source_mode" \
    EXPECTED_SOURCE_DEV="$source_dev" EXPECTED_SOURCE_INO="$source_ino" \
    EXPECTED_CANDIDATE_SHA="$candidate_sha" \
    EXPECTED_CANDIDATE_DEV="$candidate_dev" EXPECTED_CANDIDATE_INO="$candidate_ino" \
    python3 - "$cfg" "$MIGRATION_CANDIDATE" "$DRY_RUN" <<'PY' || rc=$?
import hashlib
import os
import stat
import sys
from pathlib import Path

cfg = Path(sys.argv[1])
candidate = Path(sys.argv[2])
dry_run = sys.argv[3] == "1"


class PublicationRefusal(RuntimeError):
    pass


def expected_inode(prefix: str) -> tuple[int, int]:
    try:
        return int(os.environ[f"EXPECTED_{prefix}_DEV"]), int(
            os.environ[f"EXPECTED_{prefix}_INO"]
        )
    except (KeyError, ValueError) as exc:
        raise PublicationRefusal("migration metadata was invalid") from exc


def matches_inode(result: os.stat_result, expected: tuple[int, int]) -> bool:
    return (result.st_dev, result.st_ino) == expected


def assert_named_inode(
    path: Path,
    expected: tuple[int, int],
    what: str,
    *,
    single_link: bool = False,
) -> os.stat_result:
    result = os.lstat(path)
    if not stat.S_ISREG(result.st_mode) or result.st_uid != os.geteuid():
        raise PublicationRefusal(f"{what} changed to an unsafe inode")
    if single_link and result.st_nlink != 1:
        raise PublicationRefusal(f"{what} changed to a linked inode")
    if not matches_inode(result, expected):
        raise PublicationRefusal(f"{what} path was swapped while candidate was being validated")
    return result


def open_expected(
    path: Path,
    expected: tuple[int, int],
    flags: int,
    what: str,
    *,
    single_link: bool = False,
) -> tuple[int, os.stat_result]:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise OSError("O_NOFOLLOW is unavailable")
    descriptor = os.open(os.fspath(path), flags | nofollow)
    try:
        result = os.fstat(descriptor)
        if not stat.S_ISREG(result.st_mode) or result.st_uid != os.geteuid():
            raise PublicationRefusal(f"{what} changed to an unsafe inode")
        if single_link and result.st_nlink != 1:
            raise PublicationRefusal(f"{what} changed to a linked inode")
        if not matches_inode(result, expected):
            raise PublicationRefusal(f"{what} path was swapped while candidate was being validated")
        # Check the name after opening its descriptor. This catches a rename
        # between lstat/open and keeps the descriptor bound to the mktemp inode.
        named = assert_named_inode(path, expected, what)
        if single_link and named.st_nlink != 1:
            raise PublicationRefusal(f"{what} changed to a linked inode")
        return descriptor, result
    except BaseException:
        os.close(descriptor)
        raise


def read_expected(path: Path, expected: tuple[int, int], what: str) -> tuple[bytes, os.stat_result]:
    descriptor, result = open_expected(path, expected, os.O_RDONLY, what)
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks), result
            chunks.append(chunk)
    finally:
        os.close(descriptor)


try:
    expected_source_mode = int(os.environ["EXPECTED_SOURCE_MODE"], 8)
    if not 0 <= expected_source_mode <= 0o7777:
        raise ValueError
    source_inode = expected_inode("SOURCE")
    candidate_inode = expected_inode("CANDIDATE")
    source, source_stat = read_expected(cfg, source_inode, "config")
    candidate_bytes, candidate_stat = read_expected(
        candidate,
        candidate_inode,
        "candidate",
    )
    if candidate_stat.st_nlink != 1:
        raise PublicationRefusal("candidate changed to a linked inode")
    if hashlib.sha256(source).hexdigest() != os.environ["EXPECTED_SOURCE_SHA"]:
        print("migration refused: config changed while its candidate was being validated", file=sys.stderr)
        sys.exit(5)
    if stat.S_IMODE(source_stat.st_mode) != expected_source_mode:
        print(
            "migration refused: config permission mode changed while its candidate was being validated",
            file=sys.stderr,
        )
        sys.exit(5)
    if hashlib.sha256(candidate_bytes).hexdigest() != os.environ["EXPECTED_CANDIDATE_SHA"]:
        print("migration refused: validator changed the candidate", file=sys.stderr)
        sys.exit(5)

    # mktemp starts at 0600. Reassert the source mode through an O_NOFOLLOW
    # descriptor so a validator-induced mode change cannot affect another path.
    descriptor, _candidate_stat = open_expected(
        candidate,
        candidate_inode,
        os.O_WRONLY,
        "candidate",
        single_link=True,
    )
    try:
        os.fchmod(descriptor, expected_source_mode)
        os.fsync(descriptor)
        assert_named_inode(candidate, candidate_inode, "candidate", single_link=True)
    finally:
        os.close(descriptor)

    # Verify both names immediately before rename. os.replace replaces the
    # destination entry rather than following it; candidate identity has already
    # been bound to the owned inode via the descriptor checks above.
    assert_named_inode(cfg, source_inode, "config")
    assert_named_inode(candidate, candidate_inode, "candidate", single_link=True)
    if not dry_run:
        os.replace(candidate, cfg)
        try:
            directory_fd = os.open(str(cfg.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
except PublicationRefusal as exc:
    print(f"migration refused: {exc}", file=sys.stderr)
    sys.exit(5)
except (OSError, RuntimeError, ValueError):
    print("migration refused: atomic candidate publication failed", file=sys.stderr)
    sys.exit(5)
PY
  [ "$rc" = "0" ] || exit 5

  if [ "$DRY_RUN" = "1" ]; then
    log "[dry-run] validated migration candidate for $cfg -> $HOOK_ID@$HOOK_REV"
  else
    MIGRATION_CANDIDATE=""
    log "migrated atomically: $cfg -> $HOOK_ID@$HOOK_REV"
  fi
  maybe_remove_legacy_redactor "$migration_kind" "$script_referenced"
}

if [ "$MIGRATE" = "1" ]; then
  migrate_config
  # Re-running --migrate on an already-current config must not touch the repo at
  # all: no ignore rules, no hook install, no advisory writes.
  if [ "$MIGRATION_ALREADY_CURRENT" = "1" ]; then
    exit 0
  fi
else
  # 1. .pre-commit-config.yaml (validation-only remote hook; no vendored redactor)
  install_file ".pre-commit-config.yaml" \
    "$ASSETS_DIR/pre-commit-config.yaml.template" \
    "$CHEZMOI_SRC/.pre-commit-config.yaml"

  # 2. .gitleaks.toml
  install_file ".gitleaks.toml" \
    "$ASSETS_DIR/gitleaks.toml.template" \
    "$CHEZMOI_SRC/.gitleaks.toml"
fi

# 3. Keep SpecStory's machine-local state out of Git without hiding the
#    review-bearing .specstory/history/*.md files. Merge exact, nested rules
#    instead of ignoring the whole .specstory directory. Idempotent.
append_specstory_ignore_rule() {
  local file="$1" pattern="$2" comment="$3"
  if [ -f "$file" ] && grep -Fqx "$pattern" "$file"; then
    return 0
  fi

  if [ "$DRY_RUN" = "1" ]; then
    log "[dry-run] add '$pattern' to $file"
    return 0
  fi

  mkdir -p "$(dirname "$file")"
  if [ -s "$file" ]; then
    # Preserve hand-written content, including a file missing its final newline.
    if [ "$(tail -c 1 "$file" | wc -l | tr -d '[:space:]')" = "0" ]; then
      printf '\n' >> "$file"
    fi
    printf '\n' >> "$file"
  fi
  printf '%s\n%s\n' "$comment" "$pattern" >> "$file"
  log "updated: $file (added $pattern)"
}

ensure_specstory_gitignore() {
  local file=".specstory/.gitignore"
  append_specstory_ignore_rule "$file" "/.project.json" \
    "# SpecStory machine-local project identity"
  append_specstory_ignore_rule "$file" "/statistics.json" \
    "# SpecStory generated session statistics"
}

handle_tracked_specstory_state() {
  local path
  local tracked=()
  for path in .specstory/.project.json .specstory/statistics.json; do
    if git ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
      tracked+=("$path")
    fi
  done
  [ "${#tracked[@]}" -gt 0 ] || return 0

  if [ "$UNTRACK_SPECSTORY_STATE" = "1" ]; then
    if [ "$DRY_RUN" = "1" ]; then
      log "[dry-run] git rm --cached -- ${tracked[*]}"
    else
      git rm --cached -- "${tracked[@]}" >/dev/null
      log "untracked SpecStory machine state (files remain on disk): ${tracked[*]}"
    fi
    return 0
  fi

  warn "SpecStory machine state is already tracked, so .gitignore cannot hide it:"
  for path in "${tracked[@]}"; do
    warn "  $path"
  done
  warn "  Re-run with --untrack-specstory-state to keep the files locally and stage their Git removals."
}

ensure_specstory_gitignore
handle_tracked_specstory_state

# 4. `pre-commit install`. Prefer a local `pre-commit` binary; fall back to
#    the same isolated major-pinned uvx spec used for candidate validation.
#    Skipped if `core.hooksPath` is set globally (chezmoi pattern) — the
#    global hook wrapper already runs `.pre-commit-config.yaml` for us.
install_hooks() {
  if [ "$DRY_RUN" = "1" ]; then
    log "[dry-run] pre-commit install (or skip if core.hooksPath set)"
    return 0
  fi

  # A genuinely redirected core.hooksPath is expected to provide its own global
  # wrapper. A configured value resolving to the default hooks directory is not
  # a redirect and should receive the normal pre-commit install.
  if [ "$HOOKS_REDIRECTED" = "1" ]; then
    log "core.hooksPath is redirected to '$configured_hooks_path' — skipping \`pre-commit install\`."
    log "  Your global hook wrapper should run the repo's .pre-commit-config.yaml automatically."
    log "  To use per-repo hooks instead, run: git config --unset core.hooksPath"
    return 0
  fi

  if command -v pre-commit >/dev/null 2>&1; then
    pre-commit install || return $?
    return 0
  fi
  if command -v uvx >/dev/null 2>&1; then
    log "pre-commit not found — using uvx $PRE_COMMIT_UVX_SPEC fallback"
    uvx "$PRE_COMMIT_UVX_SPEC" install || return $?
    return 0
  fi
  warn "neither pre-commit nor uvx available; install pre-commit manually"
  warn "  macOS: brew install pre-commit"
  warn "  or:    pipx install pre-commit"
  return 4
}
install_hooks || rc=$?
if [ "${rc:-0}" != "0" ]; then
  exit "$rc"
fi

# 5. Optional validation-only prepare-commit-msg hook. It never mutates an
#    index: explicit selectors are checked against the commit's current
#    GIT_INDEX_FILE, including Git's temporary -a/--only indexes.
if [ "$INSTALL_HOOK" = "1" ]; then
  hook_path="$(git rev-parse --git-path hooks/prepare-commit-msg)"
  if [ "$DRY_RUN" = "1" ]; then
    log "[dry-run] write $hook_path + chmod +x (explicit AGENT_HISTORY_* selectors only)"
  else
    if [ -e "$hook_path" ] && [ "$FORCE" != "1" ]; then
      warn "$hook_path already exists (use --force to overwrite)"
    else
      mkdir -p "$(dirname "$hook_path")"
      printf -v stage_script_quoted '%q' "$SCRIPT_DIR/stage-agent-artifacts.sh"
      {
        printf '%s\n' '#!/usr/bin/env bash'
        printf '%s\n' '# Installed by agent-history-hygiene bootstrap-project.sh --install-hook.'
        printf '%s\n' '# Validation-only: AGENT_HISTORY_* identity and plan policy must already be staged.'
        printf '%s\n' 'set -eu'
        printf 'stage_script=%s\n' "$stage_script_quoted"
        cat <<'HOOK'

if [ -z "${AGENT_HISTORY_SESSION_ID:-}" ] && [ -z "${AGENT_HISTORY_SPECSTORY_PATH:-}" ]; then
  printf '%s\n' 'agent-history: no AGENT_HISTORY_SESSION_ID or AGENT_HISTORY_SPECSTORY_PATH; skipping staged-artifact validation.' >&2
  exit 0
fi

args=(--session-only)
[ -z "${AGENT_HISTORY_SESSION_ID:-}" ] || args+=(--session-id "$AGENT_HISTORY_SESSION_ID")
[ -z "${AGENT_HISTORY_SPECSTORY_PATH:-}" ] || args+=(--specstory-path "$AGENT_HISTORY_SPECSTORY_PATH")

case "${AGENT_HISTORY_NO_SPECSTORY:-}" in
  "") ;;
  1) args+=(--no-specstory) ;;
  *) printf '%s\n' 'agent-history: AGENT_HISTORY_NO_SPECSTORY must be 1 or unset.' >&2; exit 1 ;;
esac

[ -z "${AGENT_HISTORY_PLAN:-}" ] || args+=(--plan "$AGENT_HISTORY_PLAN")
case "${AGENT_HISTORY_NO_PLAN:-}" in
  "") ;;
  1) args+=(--no-plan) ;;
  *) printf '%s\n' 'agent-history: AGENT_HISTORY_NO_PLAN must be 1 or unset.' >&2; exit 1 ;;
esac

if bash "$stage_script" "${args[@]}" --check-staged; then
  exit 0
else
  verify_rc=$?
fi

printf '%s\n' \
  'agent-history: commit index is missing exact artifacts or staged feature code.' \
  'agent-history: stage feature paths, then run this exact staging command:' >&2
printf '  bash %q' "$stage_script" >&2
for arg in "${args[@]}"; do printf ' %q' "$arg" >&2; done
printf '%s\n' '' \
  'agent-history: retry with a normal git commit; -a/--only may exclude artifacts.' >&2
exit "$verify_rc"
HOOK
      } > "$hook_path"
      chmod +x "$hook_path"
      log "installed: $hook_path (validation-only exact gate; visible no-op without identity)"
    fi
  fi
fi

# 6. Audit .gitignore / .git/info/exclude — warn if any artifact dir is
#    silently hidden so pre-commit won't actually see those files.
audit_ignore() {
  local dirs_file="$ASSETS_DIR/artifact-dirs.txt"
  [ -f "$dirs_file" ] || return 0
  local dir
  while IFS= read -r line; do
    line="${line%%#*}"
    line="$(printf '%s' "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [ -z "$line" ] && continue
    dir="$line"
    # check-ignore returns 0 when the path matches an ignore rule.
    if git check-ignore -q "$dir" 2>/dev/null; then
      warn "$dir is gitignored — staged files under it won't be committed"
      warn "  Check .gitignore and .git/info/exclude for a matching rule."
    fi
  done < "$dirs_file"
}
audit_ignore

# 7. Verify ~/.claude/settings.json has plansDirectory set. Don't silently
#    edit — print the patch for the user.
check_plans_directory() {
  local settings="$HOME/.claude/settings.json"
  [ -f "$settings" ] || {
    log ""
    log "note: ~/.claude/settings.json not found. To keep Claude Code plan"
    log "      files inside each project, create it with:"
    log ""
    log '      { "plansDirectory": "./.claude/plans" }'
    log ""
    return 0
  }
  if grep -q '"plansDirectory"' "$settings"; then
    return 0
  fi
  log ""
  log "note: ~/.claude/settings.json exists but does NOT set plansDirectory."
  log '      Add:  "plansDirectory": "./.claude/plans"'
  log "      so Claude Code writes plan files inside each project (committable"
  log "      with the feature diff)."
  log ""
}
check_plans_directory

log ""
log "Bootstrap complete. Recommended next steps:"
log "  1. Review .pre-commit-config.yaml + .gitleaks.toml for project-specific tweaks."
log "  2. pre-commit run --all-files      # shake out any existing issues"
log "  3. Have the agent commit code + chat together; scan with:"
log "     bash $SCRIPT_DIR/scan-staged.sh"
