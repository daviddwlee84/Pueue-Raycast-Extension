#!/usr/bin/env bash
# Scaffold a Raycast extension that is gated from the first commit.
#
# Raycast's own "Create Extension" command generates a working extension. What it
# does NOT give you is the four-stage gate — and `ray build` bundles with esbuild,
# which strips types without checking them, so an ungated extension ships type
# errors silently. This script lays down the gate along with the manifest.
set -euo pipefail

VERSION="1.0.0"

usage() {
  cat <<'EOF'
Usage: new-raycast-extension.sh --dir DIR --name NAME [OPTIONS]

Scaffold a Raycast extension with the four-stage gate (tsc --noEmit, a dev-check
harness, ray lint, ray build) already wired into a Justfile.

Required:
  --dir DIR                  Target directory (created if absent)
  --name NAME                Manifest name / store slug, kebab-case

Options:
  --title TITLE              Display title (default: Title Case of NAME)
  --author USERNAME          Your registered Raycast username (default: __AUTHOR__)
  --description TEXT         One-sentence store description
  --command NAME:MODE[:TITLE]  Repeatable. MODE is view | no-view | menu-bar.
                             Defaults to one `--command <NAME>:view`.
  --license SPDX             Default: MIT
  --no-verify-harness        Skip src/lib/dev-check.ts and the `verify` recipe
  --dry-run                  Print the plan as JSON; write nothing
  --force                    Write into a non-empty directory
  --json                     Emit the result object on stdout (default)
  --version                  Print the script version and exit
  --help, -h                 Show this help and exit

Examples:
  new-raycast-extension.sh --dir ./gh-queue --name gh-queue --author octocat \
    --command tasks:view --command "queue-menu:menu-bar:Queue Menu Bar"
  new-raycast-extension.sh --dir ./x --name x --dry-run

Output:
  One JSON object on stdout: {dir, name, files[], commands[], next_steps[]}.
  Prose goes to stderr.

Notes:
  Does NOT run npm install — no network, no side effects outside DIR. `npm run
  build` is what generates raycast-env.d.ts; it is listed in next_steps.

Exit codes:
  0  scaffolded (or --dry-run planned)
  1  invalid arguments
  2  target directory is not empty and --force was not given
  3  the skill's bundled assets/ are missing or unreadable
  4  post-write self-check failed
EOF
}

die() { printf '%s\n' "$*" >&2; exit 1; }
log() { printf '%s\n' "$*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASSETS="$SCRIPT_DIR/../assets"

DIR=""; NAME=""; TITLE=""; AUTHOR="__AUTHOR__"; DESCRIPTION=""; LICENSE="MIT"
DRY_RUN=0; FORCE=0; HARNESS=1
CMD_SPECS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --dir)                 [ $# -ge 2 ] || die "--dir needs a value"; DIR="$2"; shift ;;
    --name)                [ $# -ge 2 ] || die "--name needs a value"; NAME="$2"; shift ;;
    --title)               [ $# -ge 2 ] || die "--title needs a value"; TITLE="$2"; shift ;;
    --author)              [ $# -ge 2 ] || die "--author needs a value"; AUTHOR="$2"; shift ;;
    --description)         [ $# -ge 2 ] || die "--description needs a value"; DESCRIPTION="$2"; shift ;;
    --command)             [ $# -ge 2 ] || die "--command needs a value"; CMD_SPECS[${#CMD_SPECS[@]}]="$2"; shift ;;
    --license)             [ $# -ge 2 ] || die "--license needs a value"; LICENSE="$2"; shift ;;
    --no-verify-harness)   HARNESS=0 ;;
    --dry-run)             DRY_RUN=1 ;;
    --force)               FORCE=1 ;;
    --json)                : ;;   # the default; accepted for symmetry
    --version)             printf '%s\n' "$VERSION"; exit 0 ;;
    --help|-h)             usage; exit 0 ;;
    *)                     usage >&2; die "unknown argument: $1 (see --help)" ;;
  esac
  shift
done

[ -n "$DIR" ]  || { usage >&2; die "--dir is required"; }
[ -n "$NAME" ] || { usage >&2; die "--name is required"; }

printf '%s' "$NAME" | grep -Eq '^[a-z0-9]+(-[a-z0-9]+)*$' \
  || die "--name must be kebab-case (lowercase letters, digits, single hyphens): got \"$NAME\""

[ -d "$ASSETS" ] || { printf 'bundled assets not found at %s\n' "$ASSETS" >&2; exit 3; }
for a in package.json.template tsconfig.json.template eslint.config.mjs.template \
         Justfile.template metadata-README.md.template extension-icon.placeholder.png; do
  [ -r "$ASSETS/$a" ] || { printf 'missing bundled asset: %s\n' "$ASSETS/$a" >&2; exit 3; }
done
if [ "$HARNESS" -eq 1 ]; then
  [ -r "$ASSETS/dev-check.ts.template" ] || { printf 'missing bundled asset: dev-check.ts.template\n' >&2; exit 3; }
fi

titlecase() { printf '%s' "$1" | tr '-' ' ' | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) substr($i,2)}1'; }
[ -n "$TITLE" ] || TITLE="$(titlecase "$NAME")"
# The manifest schema enforces minLength 16 on the extension description and 12
# on each command description, so the defaults have to be sentences.
[ -n "$DESCRIPTION" ] || DESCRIPTION="$TITLE — a Raycast extension."

[ ${#CMD_SPECS[@]} -gt 0 ] || CMD_SPECS[0]="$NAME:view"

CMD_NAMES=(); CMD_MODES=(); CMD_TITLES=()
i=0
for spec in "${CMD_SPECS[@]}"; do
  cname=$(printf '%s' "$spec" | cut -d: -f1)
  cmode=$(printf '%s' "$spec" | cut -d: -f2)
  ctitle=$(printf '%s' "$spec" | cut -d: -f3-)
  [ -n "$cname" ] || die "--command \"$spec\": empty command name"
  printf '%s' "$cname" | grep -Eq '^[a-z0-9]+(-[a-z0-9]+)*$' \
    || die "--command \"$spec\": command name must be kebab-case"
  case "$cmode" in
    view|no-view|menu-bar) ;;
    "") die "--command \"$spec\": missing MODE (use NAME:view, NAME:no-view, or NAME:menu-bar)" ;;
    *)  die "--command \"$spec\": MODE must be view, no-view, or menu-bar; got \"$cmode\"" ;;
  esac
  [ -n "$ctitle" ] || ctitle="$(titlecase "$cname")"
  CMD_NAMES[i]="$cname"; CMD_MODES[i]="$cmode"; CMD_TITLES[i]="$ctitle"
  i=$((i + 1))
done

HAS_MENUBAR=0
for m in "${CMD_MODES[@]}"; do [ "$m" = "menu-bar" ] && HAS_MENUBAR=1; done

# --- plan -------------------------------------------------------------------

FILES=(package.json tsconfig.json eslint.config.mjs .gitignore Justfile LICENSE
       CHANGELOG.md README.md metadata/README.md assets/extension-icon.png)
[ "$HAS_MENUBAR" -eq 1 ] && FILES[${#FILES[@]}]="assets/menu-bar-icon.svg"
for n in "${CMD_NAMES[@]}"; do FILES[${#FILES[@]}]="src/$n.tsx"; done
[ "$HARNESS" -eq 1 ] && FILES[${#FILES[@]}]="src/lib/dev-check.ts"

json_list() { # each remaining arg becomes a quoted element
  out=""
  for e in "$@"; do out="${out}${out:+,}\"$(printf '%s' "$e" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')\""; done
  printf '[%s]' "$out"
}

CMD_JSON_LIST=""
i=0
while [ $i -lt ${#CMD_NAMES[@]} ]; do
  CMD_JSON_LIST="${CMD_JSON_LIST}${CMD_JSON_LIST:+,}{\"name\":\"${CMD_NAMES[$i]}\",\"mode\":\"${CMD_MODES[$i]}\"}"
  i=$((i + 1))
done

NEXT_STEPS=("cd $DIR && npm install"
            "npm run build   # this is what GENERATES raycast-env.d.ts"
            "just check      # tsc --noEmit, verify, ray lint, ray build"
            "npm run dev, then open the command FROM RAYCAST ROOT SEARCH, not the dev console"
            "replace assets/extension-icon.png — the placeholder is rejected by review")

emit_result() {
  printf '{"dir":"%s","name":"%s","dry_run":%s,"files":%s,"commands":[%s],"next_steps":%s}\n' \
    "$DIR" "$NAME" "$([ "$DRY_RUN" -eq 1 ] && echo true || echo false)" \
    "$(json_list "${FILES[@]}")" "$CMD_JSON_LIST" "$(json_list "${NEXT_STEPS[@]}")"
}

if [ "$DRY_RUN" -eq 1 ]; then
  log "dry run — nothing written"
  emit_result
  exit 0
fi

if [ -d "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ] && [ "$FORCE" -eq 0 ]; then
  printf '%s is not empty (use --force to write into it anyway)\n' "$DIR" >&2
  exit 2
fi

# --- write ------------------------------------------------------------------

mkdir -p "$DIR/src/lib" "$DIR/assets" "$DIR/metadata"

COMMANDS_JSON=""
i=0
while [ $i -lt ${#CMD_NAMES[@]} ]; do
  entry="    {
      \"name\": \"${CMD_NAMES[$i]}\",
      \"title\": \"${CMD_TITLES[$i]}\",
      \"description\": \"Open the ${CMD_TITLES[$i]} command.\",
      \"mode\": \"${CMD_MODES[$i]}\""
  [ "${CMD_MODES[$i]}" = "menu-bar" ] && entry="$entry,
      \"interval\": \"1m\""
  entry="$entry
    }"
  COMMANDS_JSON="${COMMANDS_JSON}${COMMANDS_JSON:+,
}${entry}"
  i=$((i + 1))
done

# The commands block is multi-line, and awk -v cannot carry a newline, so it goes
# through a temp file that awk splices in at the __COMMANDS__ marker.
COMMANDS_FILE="$DIR/.commands.json.tmp"
printf '%s\n' "$COMMANDS_JSON" > "$COMMANDS_FILE"

# Substitute with awk so no placeholder value is re-scanned as a sed pattern.
subst() { # template destination
  awk -v name="$NAME" -v title="$TITLE" -v author="$AUTHOR" -v descr="$DESCRIPTION" \
      -v license="$LICENSE" -v cmdfile="$COMMANDS_FILE" '
    {
      gsub(/__NAME__/, name); gsub(/__TITLE__/, title); gsub(/__AUTHOR__/, author)
      gsub(/__DESCRIPTION__/, descr); gsub(/__LICENSE__/, license)
      if ($0 ~ /__COMMANDS__/) {
        while ((getline line < cmdfile) > 0) print line
        close(cmdfile)
        next
      }
      print
    }' "$1" > "$2"
}

subst "$ASSETS/package.json.template"        "$DIR/package.json"
rm -f "$COMMANDS_FILE"
cp    "$ASSETS/tsconfig.json.template"       "$DIR/tsconfig.json"
cp    "$ASSETS/eslint.config.mjs.template"   "$DIR/eslint.config.mjs"
cp    "$ASSETS/metadata-README.md.template"  "$DIR/metadata/README.md"
cp    "$ASSETS/extension-icon.placeholder.png" "$DIR/assets/extension-icon.png"

if [ "$HARNESS" -eq 1 ]; then
  cp "$ASSETS/dev-check.ts.template" "$DIR/src/lib/dev-check.ts"
  cp "$ASSETS/Justfile.template" "$DIR/Justfile"
else
  # Drop the verify recipe and its use in `check`.
  awk '
    /^# assert the pure modules/ { skip = 1 }
    skip && /^$/ { skip = 0; next }
    skip { next }
    /^check:/ { print "check: typecheck lint build"; next }
    { print }
  ' "$ASSETS/Justfile.template" > "$DIR/Justfile"
fi

if [ "$HAS_MENUBAR" -eq 1 ]; then
  cat > "$DIR/assets/menu-bar-icon.svg" <<'SVG'
<!-- Monochrome template. Always render it with a tintColor; the shape never
     moves in the menu bar, only its colour. -->
<svg width="16" height="16" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">
  <rect x="2" y="3"  width="12" height="2" rx="1" fill="black"/>
  <rect x="2" y="7"  width="12" height="2" rx="1" fill="black"/>
  <rect x="2" y="11" width="8"  height="2" rx="1" fill="black"/>
</svg>
SVG
fi

cat > "$DIR/.gitignore" <<'EOF'
node_modules/
.build/
.raycast-swift-build/
dist/
EOF

YEAR=$(date +%Y)
cat > "$DIR/LICENSE" <<EOF
MIT License

Copyright (c) $YEAR

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
EOF

cat > "$DIR/CHANGELOG.md" <<EOF
# $TITLE Changelog

## [Initial Version] - {PR_MERGE_DATE}

- First release.
EOF

{
  printf '# %s\n\n%s\n\n## Setup\n\n' "$TITLE" "$DESCRIPTION"
  printf '1. Install any binary this extension shells out to.\n'
  printf '2. Set its absolute path in extension preferences if it is not on the\n'
  printf '   default PATH — Raycast runs under launchd with no shell rc, so a bare\n'
  printf '   name is never found.\n'
  if [ "$HAS_MENUBAR" -eq 1 ]; then
    printf '\n## The menu bar command\n\n'
    printf 'Background refresh is **off by default for store installs**. Run the\n'
    printf 'command once, or enable it in the command settings, or the menu bar shows\n'
    printf 'nothing. This is the most common "it is broken" report.\n'
  fi
  printf '\n## Development\n\n```sh\nnpm install\njust check   # tsc --noEmit, verify, ray lint, ray build\nnpm run dev\n```\n'
} > "$DIR/README.md"

i=0
while [ $i -lt ${#CMD_NAMES[@]} ]; do
  n="${CMD_NAMES[$i]}"; m="${CMD_MODES[$i]}"; t="${CMD_TITLES[$i]}"
  case "$m" in
    view)
      cat > "$DIR/src/$n.tsx" <<EOF
import { ActionPanel, Action, List } from "@raycast/api";

export default function Command() {
  return (
    <List searchBarPlaceholder="Search…">
      <List.Item
        title="$t"
        actions={
          <ActionPanel>
            <Action.CopyToClipboard title="Copy" content="$t" />
          </ActionPanel>
        }
      />
    </List>
  );
}
EOF
      ;;
    no-view)
      cat > "$DIR/src/$n.tsx" <<EOF
import { showHUD } from "@raycast/api";

// A no-view command exports an async function, NOT a React component. There is
// no window, so feedback is a HUD rather than a toast.
export default async function Command() {
  await showHUD("$t");
}
EOF
      ;;
    menu-bar)
      cat > "$DIR/src/$n.tsx" <<EOF
import { Color, MenuBarExtra } from "@raycast/api";

export default function Command() {
  const count = 0;
  return (
    <MenuBarExtra
      icon={{ source: "menu-bar-icon.svg", tintColor: Color.PrimaryText }}
      // The count IS the title — there is no badge API, and undefined hides it.
      title={count > 0 ? String(count) : undefined}
      // isLoading is a contract: never unset (Raycast renders then unloads),
      // never stuck true (the tree re-runs every tick).
      isLoading={false}
    >
      {/* An item with no onAction is a disabled label. */}
      <MenuBarExtra.Item title="Updated just now" />
    </MenuBarExtra>
  );
}
EOF
      ;;
  esac
  i=$((i + 1))
done

# --- self-check -------------------------------------------------------------

PROBLEMS=""
for n in "${CMD_NAMES[@]}"; do
  [ -f "$DIR/src/$n.tsx" ] || PROBLEMS="${PROBLEMS}${PROBLEMS:+; }no src file for command $n"
done
if command -v node >/dev/null 2>&1; then
  node -e 'JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"))' "$DIR/package.json" 2>/dev/null \
    || PROBLEMS="${PROBLEMS}${PROBLEMS:+; }generated package.json is not valid JSON"
fi
if [ -n "$PROBLEMS" ]; then
  printf 'post-write self-check failed: %s\n' "$PROBLEMS" >&2
  exit 4
fi

log "scaffolded $NAME in $DIR"
log "next: ${NEXT_STEPS[0]}"
emit_result
