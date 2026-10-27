#!/usr/bin/env bash
# agent-commit-metadata.sh — derive canonical commit provenance trailers from
# staged agent transcripts and plan files.
#
# Bash 3.2 compatible (stock macOS).

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: agent-commit-metadata.sh [OPTIONS]

Read the staged git snapshot and emit canonical agentic-commit provenance:

  AI-Assisted-By: Codex CLI (gpt-5.6-sol)
  Agent-Transcript: .specstory/history/session.md
  Agent-Plan: .claude/plans/plan.md

The helper never edits a commit message. Append its output as the FINAL trailer
block, after any tool-native Generated-with / Co-Authored-By metadata.

Options:
  --harness NAME       Override auto-detected harness name. Requires --model.
  --model NAME         Override auto-detected model name. Requires --harness.
  --format FORMAT      trailers (default) or json.
  --help, -h           Show this help and exit.

Auto-detection reads staged SpecStory Markdown, not the live working-tree file.
If no staged transcript identifies both harness and model, pass both overrides.

Exit codes:
  0  metadata emitted
  1  invalid arguments
  2  not inside a git repository
  3  no staged transcript or plan artifacts
  4  harness/model provenance could not be resolved
EOF
}

log()  { printf '%s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$1" >&2; exit "${2:-1}"; }

HARNESS=""
MODEL=""
FORMAT="trailers"

while [ $# -gt 0 ]; do
  case "$1" in
    --harness)
      shift
      [ $# -gt 0 ] || die "--harness needs a value (try --help)" 1
      HARNESS="$1"; shift ;;
    --harness=*) HARNESS="${1#--harness=}"; shift ;;
    --model)
      shift
      [ $# -gt 0 ] || die "--model needs a value (try --help)" 1
      MODEL="$1"; shift ;;
    --model=*) MODEL="${1#--model=}"; shift ;;
    --format)
      shift
      [ $# -gt 0 ] || die "--format needs trailers or json (try --help)" 1
      FORMAT="$1"; shift ;;
    --format=*) FORMAT="${1#--format=}"; shift ;;
    --help|-h) usage; exit 0 ;;
    -*) die "unknown flag: $1 (try --help)" 1 ;;
    *)  die "unexpected positional arg: $1 (try --help)" 1 ;;
  esac
done

case "$FORMAT" in
  trailers|json) ;;
  *) die "invalid --format: $FORMAT (expected trailers|json)" 1 ;;
esac

if { [ -n "$HARNESS" ] && [ -z "$MODEL" ]; } || \
   { [ -z "$HARNESS" ] && [ -n "$MODEL" ]; }; then
  die "--harness and --model must be provided together" 1
fi

case "$HARNESS$MODEL" in
  *$'\n'*) die "harness/model values must not contain newlines" 1 ;;
esac

if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
  die "not inside a git repository" 2
fi
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

ASSISTANTS=()
TRANSCRIPTS=()
PLANS=()

append_unique() {
  local kind="$1" value="$2" existing
  case "$value" in *$'\n'*) die "artifact paths must not contain newlines: $value" 4 ;; esac
  case "$kind" in
    assistant)
      for existing in "${ASSISTANTS[@]:-}"; do [ "$existing" = "$value" ] && return 0; done
      ASSISTANTS+=("$value") ;;
    transcript)
      for existing in "${TRANSCRIPTS[@]:-}"; do [ "$existing" = "$value" ] && return 0; done
      TRANSCRIPTS+=("$value") ;;
    plan)
      for existing in "${PLANS[@]:-}"; do [ "$existing" = "$value" ] && return 0; done
      PLANS+=("$value") ;;
    *) die "internal error: unknown collection '$kind'" 4 ;;
  esac
}

normalize_harness() {
  case "$1" in
    Claude|"Claude Code"*) printf 'Claude Code' ;;
    Codex|"Codex CLI"*)    printf 'Codex CLI' ;;
    Cursor*)                printf 'Cursor' ;;
    OpenCode*)              printf 'OpenCode' ;;
    "Gemini CLI"*)         printf 'Gemini CLI' ;;
    *)                      printf '%s' "$1" ;;
  esac
}

parse_transcript() {
  local path="$1" content harness model found_model=0
  content="$(git show ":$path" 2>/dev/null || true)"
  [ -n "$content" ] || die "could not read staged transcript: $path" 4

  harness="$(printf '%s\n' "$content" | sed -n -E \
    's/^<!-- ([^<]+) Session .*/\1/p' | head -n 1)"
  harness="$(normalize_harness "$harness")"
  [ -n "$harness" ] || die "could not detect harness from staged transcript: $path (pass --harness and --model)" 4

  while IFS= read -r model; do
    [ -n "$model" ] || continue
    append_unique assistant "$harness ($model)"
    found_model=1
  done <<EOF
$(printf '%s\n' "$content" | sed -n -E \
  's/^_\*\*(Agent|Assistant) \((.*) [0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}Z\)\*\*_[[:space:]]*$/\2/p')
EOF

  [ "$found_model" = "1" ] || \
    die "could not detect a model from staged transcript: $path (pass --harness and --model)" 4
}

while IFS= read -r -d '' path; do
  case "$path" in
    .specstory/history/*.md) append_unique transcript "$path" ;;
    .claude/plans/*.md|.cursor/plans/*.md|.opencode/plans/*.md|.specify/*.md|.codex/*.md)
      append_unique plan "$path" ;;
  esac
done < <(git diff --cached --name-only --diff-filter=ACMR -z)

if [ "${#TRANSCRIPTS[@]}" -eq 0 ] && [ "${#PLANS[@]}" -eq 0 ]; then
  die "no staged agent transcript or plan artifacts (stage them first)" 3
fi

if [ -n "$HARNESS" ]; then
  append_unique assistant "$(normalize_harness "$HARNESS") ($MODEL)"
elif [ "${#TRANSCRIPTS[@]}" -gt 0 ]; then
  # Bash 3.2 with `set -u` rejects an unguarded expansion of an empty array.
  for transcript in "${TRANSCRIPTS[@]}"; do
    parse_transcript "$transcript"
  done
fi

if [ "${#ASSISTANTS[@]}" -eq 0 ]; then
  die "no harness/model provenance resolved (pass --harness and --model)" 4
fi

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

json_array() {
  local kind="$1" first=1 item
  printf '['
  case "$kind" in
    assistant)
      if [ "${#ASSISTANTS[@]}" -gt 0 ]; then set -- "${ASSISTANTS[@]}"; else set --; fi ;;
    transcript)
      if [ "${#TRANSCRIPTS[@]}" -gt 0 ]; then set -- "${TRANSCRIPTS[@]}"; else set --; fi ;;
    plan)
      if [ "${#PLANS[@]}" -gt 0 ]; then set -- "${PLANS[@]}"; else set --; fi ;;
  esac
  for item in "$@"; do
    [ "$first" = "1" ] || printf ','
    printf '"%s"' "$(json_escape "$item")"
    first=0
  done
  printf ']'
}

if [ "$FORMAT" = "json" ]; then
  printf '{"assistants":'
  json_array assistant
  printf ',"transcripts":'
  json_array transcript
  printf ',"plans":'
  json_array plan
  printf '}\n'
else
  if [ "${#ASSISTANTS[@]}" -gt 0 ]; then
    for assistant in "${ASSISTANTS[@]}"; do
      printf 'AI-Assisted-By: %s\n' "$assistant"
    done
  fi
  if [ "${#TRANSCRIPTS[@]}" -gt 0 ]; then
    for transcript in "${TRANSCRIPTS[@]}"; do
      printf 'Agent-Transcript: %s\n' "$transcript"
    done
  fi
  if [ "${#PLANS[@]}" -gt 0 ]; then
    for plan in "${PLANS[@]}"; do
      printf 'Agent-Plan: %s\n' "$plan"
    done
  fi
fi

log "metadata: ${#ASSISTANTS[@]} assistant(s), ${#TRANSCRIPTS[@]} transcript(s), ${#PLANS[@]} plan(s)"
