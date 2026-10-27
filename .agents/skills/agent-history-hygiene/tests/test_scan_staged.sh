#!/usr/bin/env bash
# test_scan_staged.sh — fail-closed, secret-safe contract for scan-staged.sh.
#
# Bash 3.2 compatible (stock macOS). Uses a fake gitleaks; no sleeps and no
# dependency on the real scanner.

set -u  # intentionally not -e: nonzero script exits are the subject of tests

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$TESTS_DIR/.." && pwd)"
SCAN="$SKILL_DIR/scripts/scan-staged.sh"
ORIGINAL_PATH="$PATH"

PASS_COUNT=0
FAIL_COUNT=0
FAIL_LOG=""
TMP_ROOT="$(mktemp -d /tmp/test-scan-staged.XXXXXX)"
FAKE_BIN="$TMP_ROOT/fake-bin"
mkdir -p "$FAKE_BIN"
trap 'rm -rf "$TMP_ROOT"' EXIT HUP INT TERM

red()   { printf '\033[31m%s\033[0m' "$*"; }
green() { printf '\033[32m%s\033[0m' "$*"; }

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  printf '  %s %s\n' "$(green PASS)" "$1"
}

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  printf '  %s %s\n' "$(red FAIL)" "$1"
  FAIL_LOG="${FAIL_LOG}FAIL: $1\n"
}

make_repo() {
  local d
  d="$(mktemp -d "$TMP_ROOT/repo.XXXXXX")"
  git -C "$d" init -q -b main
  git -C "$d" -c core.hooksPath=/dev/null \
      -c user.email=test@example.com -c user.name=test \
      commit -q --allow-empty -m init
  mkdir -p "$d/.claude/plans"
  printf '%s\n' 'staged fixture' > "$d/.claude/plans/p.md"
  git -C "$d" add -- .claude/plans/p.md
  printf '%s' "$d"
}

cat > "$FAKE_BIN/gitleaks" <<'FAKE'
#!/usr/bin/env bash
set -u

report_path=""
redact_seen=0
for arg in "$@"; do
  if [ -n "${FAKE_ARGS_FILE:-}" ]; then
    printf '%s\n' "$arg" >> "$FAKE_ARGS_FILE"
  fi
  if [ "$arg" = "--redact" ]; then
    redact_seen=1
  fi
done

while [ $# -gt 0 ]; do
  case "$1" in
    --report-path)
      report_path="${2:-}"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

[ -n "$report_path" ] || exit 8
if [ "$redact_seen" = "1" ] && [ -n "${FAKE_REDACT_MARKER:-}" ]; then
  : > "$FAKE_REDACT_MARKER"
fi

case "${FAKE_GITLEAKS_MODE:-clean}" in
  clean)
    printf '%s' '[]' > "$report_path"
    ;;
  empty)
    : > "$report_path"
    ;;
  finding)
    printf '%s' '[{"RuleID":"fixture-rule","File":".claude/plans/p.md","StartLine":2,"Commit":"","Secret":"scanner-private-value","Match":"scanner-private-value"}]' > "$report_path"
    ;;
  malformed)
    printf '%s' '{not-json' > "$report_path"
    ;;
  whitespace)
    printf '  \n' > "$report_path"
    ;;
  object)
    printf '%s' '{}' > "$report_path"
    ;;
  bad-element)
    printf '%s' '[42]' > "$report_path"
    ;;
  barrier)
    : > "$FAKE_BARRIER_READY"
    while [ ! -e "$FAKE_BARRIER_RELEASE" ]; do sleep 0.02; done
    printf '%s' '[]' > "$report_path"
    : > "$FAKE_BARRIER_DONE"
    ;;
  error)
    printf '%s\n' 'scanner-private-diagnostic' >&2
    exit 9
    ;;
  *)
    exit 8
    ;;
esac
exit 0
FAKE
chmod +x "$FAKE_BIN/gitleaks"

run_scan() {
  local repo="$1" mode="$2" stdout_file="$3" stderr_file="$4"
  shift 4
  (
    cd "$repo" || exit 99
    PATH="$FAKE_BIN:$ORIGINAL_PATH" \
      FAKE_GITLEAKS_MODE="$mode" \
      FAKE_ARGS_FILE="${FAKE_ARGS_FILE:-}" \
      FAKE_REDACT_MARKER="${FAKE_REDACT_MARKER:-}" \
      bash "$SCAN" "$@" > "$stdout_file" 2> "$stderr_file"
  )
}

contains_text() {
  local file="$1" text="$2"
  grep -F -- "$text" "$file" >/dev/null 2>&1
}

printf '== scan-staged fail-closed contract ==\n\n'

# 1. Valid empty list is clean.
REPO="$(make_repo)"
OUT="$TMP_ROOT/clean.out"; ERR="$TMP_ROOT/clean.err"
run_scan "$REPO" clean "$OUT" "$ERR"
rc=$?
if [ "$rc" = "0" ] && [ ! -s "$OUT" ]; then
  pass "valid empty report -> exit 0 with no stdout"
else
  fail "valid empty report -> exit 0 with no stdout (got $rc)"
fi

# 2. A zero-byte clean report remains supported.
OUT="$TMP_ROOT/empty.out"; ERR="$TMP_ROOT/empty.err"
run_scan "$REPO" empty "$OUT" "$ERR"
rc=$?
if [ "$rc" = "0" ] && [ ! -s "$OUT" ]; then
  pass "zero-byte clean report -> exit 0"
else
  fail "zero-byte clean report -> exit 0 (got $rc)"
fi

# 3. Default mode passes --redact and returns secret-free JSONL with exit 10.
OUT="$TMP_ROOT/finding.out"; ERR="$TMP_ROOT/finding.err"
ARGS="$TMP_ROOT/finding.args"; MARKER="$TMP_ROOT/redact.seen"
: > "$ARGS"
FAKE_ARGS_FILE="$ARGS" FAKE_REDACT_MARKER="$MARKER" \
  run_scan "$REPO" finding "$OUT" "$ERR"
rc=$?
unset FAKE_ARGS_FILE FAKE_REDACT_MARKER
json_ok=1
python3 - "$OUT" <<'PY' >/dev/null 2>&1 || json_ok=0
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    lines = source.read().splitlines()
assert len(lines) == 1
finding = json.loads(lines[0])
assert set(finding) == {"rule_id", "file", "line", "commit"}
assert finding["rule_id"] == "fixture-rule"
assert finding["file"] == ".claude/plans/p.md"
assert finding["line"] == 2
assert finding["commit"] == "STAGED"
PY
if [ "$rc" = "10" ] && [ -e "$MARKER" ] && \
   grep -Fx -- '--redact' "$ARGS" >/dev/null 2>&1 && \
   [ "$json_ok" = "1" ] && \
   ! contains_text "$OUT" 'scanner-private-value' && \
   ! contains_text "$ERR" 'scanner-private-value' && \
   contains_text "$ERR" 'masks output only'; then
  pass "default finding -> exit 10, --redact, secret-free JSONL"
else
  fail "default finding contract (got exit $rc, json_ok=$json_ok)"
fi

# 4. Explicit opt-out preserves exit 20 but still omits sensitive fields.
OUT="$TMP_ROOT/no-redact.out"; ERR="$TMP_ROOT/no-redact.err"
MARKER="$TMP_ROOT/no-redact.seen"
FAKE_REDACT_MARKER="$MARKER" run_scan "$REPO" finding "$OUT" "$ERR" --no-redact
rc=$?
unset FAKE_REDACT_MARKER
if [ "$rc" = "20" ] && [ ! -e "$MARKER" ] && \
   ! contains_text "$OUT" 'scanner-private-value' && \
   ! contains_text "$OUT" '"match"' && \
   ! contains_text "$OUT" '"secret"'; then
  pass "--no-redact finding -> exit 20 without Secret/Match output"
else
  fail "--no-redact finding contract (got $rc)"
fi

# 5. Scanner execution errors fail closed and never replay scanner stderr.
OUT="$TMP_ROOT/error.out"; ERR="$TMP_ROOT/error.err"
run_scan "$REPO" error "$OUT" "$ERR" --verbose
rc=$?
if [ "$rc" = "40" ] && \
   ! contains_text "$OUT" 'scanner-private-diagnostic' && \
   ! contains_text "$ERR" 'scanner-private-diagnostic' && \
   contains_text "$ERR" 'diagnostics suppressed'; then
  pass "scanner error -> exit 40 with stderr suppressed"
else
  fail "scanner error -> safe exit 40 (got $rc)"
fi

# 6-9. Every malformed/non-list shape is an operational error, never clean.
for mode in malformed whitespace object bad-element; do
  OUT="$TMP_ROOT/$mode.out"; ERR="$TMP_ROOT/$mode.err"
  run_scan "$REPO" "$mode" "$OUT" "$ERR"
  rc=$?
  if [ "$rc" = "40" ] && [ ! -s "$OUT" ]; then
    pass "$mode report -> exit 40 with no stdout"
  else
    fail "$mode report -> exit 40 with no stdout (got $rc)"
  fi
done

# A scanner barrier holds execution after both private report files exist. Each
# trapped signal must clean those files and exit 128+signal, never resume and
# interpret the scanner's eventual empty report as a clean pass.
for signal_case in 'HUP 129' 'INT 130' 'TERM 143'; do
  signal_name="${signal_case%% *}"
  expected_rc="${signal_case##* }"
  signal_sync="$TMP_ROOT/signal-$signal_name"
  signal_reports="$signal_sync/reports"
  mkdir -p "$signal_reports"
  python3 - "$SCAN" "$REPO" "$FAKE_BIN" "$ORIGINAL_PATH" \
    "$signal_sync" "$signal_reports" "$signal_name" "$expected_rc" <<'PY'
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

scan, repo, fake_bin, original_path, sync_raw, reports_raw, name, expected_raw = sys.argv[1:]
sync = Path(sync_raw)
reports = Path(reports_raw)
ready = sync / "ready"
release = sync / "release"
done = sync / "done"
env = os.environ.copy()
env.update(
    {
        "PATH": fake_bin + os.pathsep + original_path,
        "TMPDIR": str(reports),
        "FAKE_GITLEAKS_MODE": "barrier",
        "FAKE_BARRIER_READY": str(ready),
        "FAKE_BARRIER_RELEASE": str(release),
        "FAKE_BARRIER_DONE": str(done),
    }
)
process = subprocess.Popen(
    ["/bin/bash", scan],
    cwd=repo,
    env=env,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

def wait_for(path: Path, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.01)
    return False

ok = wait_for(ready)
if ok:
    os.kill(process.pid, getattr(signal, "SIG" + name))
release.touch()
ok = wait_for(done) and ok
try:
    returncode = process.wait(timeout=5.0)
except subprocess.TimeoutExpired:
    process.kill()
    process.wait()
    returncode = -1
time.sleep(0.05)
leftovers = list(reports.glob("gitleaks-*"))
raise SystemExit(0 if ok and returncode == int(expected_raw) and not leftovers else 1)
PY
  signal_test_rc=$?
  if [ "$signal_test_rc" = "0" ]; then
    pass "$signal_name -> exit $expected_rc after private-file cleanup"
  else
    fail "$signal_name -> expected signal exit after private-file cleanup"
  fi
done

# Missing scanner retains exit 30.
OUT="$TMP_ROOT/missing.out"; ERR="$TMP_ROOT/missing.err"
MISSING_BIN="$TMP_ROOT/missing-bin"
mkdir "$MISSING_BIN"
(
  cd "$REPO" || exit 99
  PATH="$MISSING_BIN" /bin/bash "$SCAN" > "$OUT" 2> "$ERR"
)
rc=$?
if [ "$rc" = "30" ]; then
  pass "missing gitleaks -> exit 30"
else
  fail "missing gitleaks -> exit 30 (got $rc)"
fi

# 11. With a fake scanner present, an outside directory retains exit 2.
OUTSIDE="$TMP_ROOT/outside"
mkdir "$OUTSIDE"
OUT="$TMP_ROOT/outside.out"; ERR="$TMP_ROOT/outside.err"
run_scan "$OUTSIDE" clean "$OUT" "$ERR"
rc=$?
if [ "$rc" = "2" ]; then
  pass "outside git repo -> exit 2"
else
  fail "outside git repo -> exit 2 (got $rc)"
fi

# 12. Missing --config value is an argument error, not a shell shift failure.
OUT="$TMP_ROOT/config.out"; ERR="$TMP_ROOT/config.err"
run_scan "$REPO" clean "$OUT" "$ERR" --config
rc=$?
if [ "$rc" = "1" ]; then
  pass "missing --config value -> exit 1"
else
  fail "missing --config value -> exit 1 (got $rc)"
fi

# 13. Help states the masking-only semantics and the default.
OUT="$TMP_ROOT/help.out"; ERR="$TMP_ROOT/help.err"
(
  cd "$REPO" || exit 99
  PATH="$FAKE_BIN:$ORIGINAL_PATH" bash "$SCAN" --help > "$OUT" 2> "$ERR"
)
rc=$?
HELP_NORMALIZED="$TMP_ROOT/help.normalized"
tr '\n' ' ' < "$OUT" | tr -s ' ' > "$HELP_NORMALIZED"
if [ "$rc" = "0" ] && contains_text "$HELP_NORMALIZED" '(default)' && \
   contains_text "$HELP_NORMALIZED" 'never edits files' && \
   contains_text "$HELP_NORMALIZED" 'Git index' && \
   contains_text "$HELP_NORMALIZED" 'unchanged parent lines and history are not re-audited' && \
   contains_text "$HELP_NORMALIZED" 'full staged addition' && \
   contains_text "$HELP_NORMALIZED" 'not a full-index/history clean guarantee'; then
  pass "help documents masking and staged-diff scope without mutation"
else
  fail "help documents masking and staged-diff scope without mutation (got $rc)"
fi

printf '\n== summary ==\n'
printf 'pass: %d\nfail: %d\n' "$PASS_COUNT" "$FAIL_COUNT"
if [ "$FAIL_COUNT" -gt 0 ]; then
  printf '\n%b' "$FAIL_LOG"
  exit 1
fi
exit 0
