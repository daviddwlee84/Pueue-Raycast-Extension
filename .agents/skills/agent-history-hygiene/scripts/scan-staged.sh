#!/usr/bin/env bash
# scan-staged.sh — run gitleaks on the effective staged index with
# agent-friendly exit codes and secret-safe output.
#
# Bash 3.2 compatible (stock macOS).

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scan-staged.sh [OPTIONS]

Check newly staged secret reachability with `gitleaks git --staged`. The
inherited GIT_INDEX_FILE is honored, including an existing alternate-index
transaction. Modified files are diff-scanned with commit-scoped config and
.gitleaksignore semantics; unchanged parent lines and history are not re-audited.
A selected new/untracked file is scanned as a full staged addition.

Options:
  --redact           Mask secret fields in gitleaks' report (default). This
                     changes scanner output only; it never edits files or the
                     Git index.
  --no-redact        Disable gitleaks output masking. The wrapper still never
                     emits Secret or Match fields.
  --config PATH      Path to .gitleaks.toml (default: repo root if present).
  --verbose          Print bounded wrapper progress; scanner output remains
                     suppressed because it may contain secret material.
  --help, -h         Show this help and exit.

Output (stdout):
  - If no newly staged findings: no output. This is not a full-index/history
    clean guarantee; pre-existing secrets require remediation/history audit.
  - If findings are found: one secret-free JSON object per finding.

Exit codes:
  0   no newly staged gitleaks findings
  10  leaks found; gitleaks output masking was enabled (default)
  20  leaks found; gitleaks output masking was explicitly disabled
  30  gitleaks binary not installed
  40  gitleaks execution/report error (including malformed JSON)
  1   invalid arguments
  2   not inside a git repo
EOF
}

log() { printf '%s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$1" >&2; exit "${2:-1}"; }

REDACT=1
CONFIG=""
VERBOSE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --redact)
      REDACT=1
      shift
      ;;
    --no-redact)
      REDACT=0
      shift
      ;;
    --config)
      [ $# -ge 2 ] || die "--config requires a path" 1
      [ -n "$2" ] || die "--config requires a non-empty path" 1
      CONFIG="$2"
      shift 2
      ;;
    --config=*)
      CONFIG="${1#--config=}"
      [ -n "$CONFIG" ] || die "--config requires a non-empty path" 1
      shift
      ;;
    --verbose)
      VERBOSE=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    -*)
      die "unknown flag (try --help)" 1
      ;;
    *)
      die "unexpected positional argument (try --help)" 1
      ;;
  esac
done

if ! command -v gitleaks >/dev/null 2>&1; then
  log "gitleaks not installed. Install hints:"
  log "  macOS:   brew install gitleaks"
  log "  Linux:   https://github.com/gitleaks/gitleaks/releases"
  exit 30
fi

if ! repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  die "not inside a git repo" 2
fi

# A relative alternate-index path is relative to the caller's directory. Make
# it absolute before changing to the repository root; an absolute inherited
# path (the stage-agent-artifacts transaction) passes through byte-for-byte.
if [ -n "${GIT_INDEX_FILE:-}" ]; then
  case "$GIT_INDEX_FILE" in
    /*) ;;
    *) GIT_INDEX_FILE="$(pwd -P)/$GIT_INDEX_FILE"; export GIT_INDEX_FILE ;;
  esac
fi
cd "$repo_root"

if [ -z "$CONFIG" ] && [ -f ".gitleaks.toml" ]; then
  CONFIG=".gitleaks.toml"
fi

report_path="$(mktemp "${TMPDIR:-/tmp}/gitleaks-report.XXXXXX")" || \
  die "could not create a private report file" 40
safe_path="$(mktemp "${TMPDIR:-/tmp}/gitleaks-safe.XXXXXX")" || {
  rm -f "$report_path"
  die "could not create a private output file" 40
}
cleanup_private_files() {
  rm -f "$report_path" "$safe_path"
}
exit_for_signal() {
  local signal_number="$1"
  trap - EXIT HUP INT TERM
  cleanup_private_files
  exit $((128 + signal_number))
}
trap 'cleanup_private_files' EXIT
trap 'exit_for_signal 1' HUP
trap 'exit_for_signal 2' INT
trap 'exit_for_signal 15' TERM

cmd=(gitleaks git --staged
     --report-format json
     --report-path "$report_path"
     --exit-code 0)
[ -n "$CONFIG" ] && cmd+=(--config "$CONFIG")
[ "$REDACT" = "1" ] && cmd+=(--redact)

[ "$VERBOSE" = "1" ] && \
  log "Running staged-diff gitleaks gate; scanner stdout/stderr are suppressed."

gl_rc=0
"${cmd[@]}" >/dev/null 2>/dev/null || gl_rc=$?
if [ "$gl_rc" -ne 0 ]; then
  log "gitleaks failed to run (exit $gl_rc); scanner diagnostics suppressed."
  exit 40
fi

# Gitleaks may leave a zero-byte file when clean. Every non-empty report must
# be validated as a JSON list before anything is emitted.
if [ ! -s "$report_path" ]; then
  exit 0
fi
if ! command -v python3 >/dev/null 2>&1; then
  log "python3 is required to validate the gitleaks JSON report."
  exit 40
fi

json_rc=0
python3 - "$report_path" "$safe_path" <<'PY' >/dev/null 2>/dev/null || json_rc=$?
import json
import os
import re
import sys

MAX_REPORT_BYTES = 16 * 1024 * 1024
report_path, safe_path = sys.argv[1:]

try:
    if os.path.getsize(report_path) > MAX_REPORT_BYTES:
        raise ValueError("oversized")
    with open(report_path, "rb") as source:
        raw = source.read()
    data = json.loads(raw.decode("utf-8"))
except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
    raise SystemExit(3)

if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
    raise SystemExit(3)

safe_findings = []
for finding in data:
    path = finding.get("File")
    if not isinstance(path, str) or not path or len(path) > 4096:
        raise SystemExit(3)
    if path.startswith("/") or path.endswith("/") or "\x00" in path:
        raise SystemExit(3)
    if any(ord(char) < 32 or ord(char) == 127 for char in path):
        raise SystemExit(3)
    components = path.split("/")
    if any(component in ("", ".", "..") for component in components):
        raise SystemExit(3)

    rule_id = finding.get("RuleID")
    if not isinstance(rule_id, str) or re.fullmatch(
        r"[A-Za-z0-9._-]{1,80}", rule_id
    ) is None:
        rule_id = "unknown"

    line = finding.get("StartLine")
    if isinstance(line, bool) or not isinstance(line, int) or not (0 < line < 2**31):
        line = None

    commit = finding.get("Commit") or "STAGED"
    if not isinstance(commit, str) or re.fullmatch(
        r"(?:STAGED|[0-9A-Fa-f]{7,64})", commit
    ) is None:
        commit = "STAGED"

    safe_findings.append(
        {"rule_id": rule_id, "file": path, "line": line, "commit": commit}
    )

try:
    with open(safe_path, "w", encoding="utf-8", newline="\n") as target:
        for finding in safe_findings:
            target.write(json.dumps(finding, ensure_ascii=True, separators=(",", ":")))
            target.write("\n")
except OSError:
    raise SystemExit(3)
PY

if [ "$json_rc" -ne 0 ]; then
  log "gitleaks returned malformed or unsafe non-list JSON."
  exit 40
fi

if [ ! -s "$safe_path" ]; then
  exit 0
fi

while IFS= read -r finding || [ -n "$finding" ]; do
  printf '%s\n' "$finding"
done < "$safe_path"

if [ "$REDACT" = "1" ]; then
  log "gitleaks found leaks; scanner output masking was enabled."
  log "NOTE: --redact masks output only; it never changes files or the Git index."
  exit 10
fi

log "gitleaks found leaks; output masking was explicitly disabled."
log "The wrapper still omitted Secret and Match fields from public output."
exit 20
