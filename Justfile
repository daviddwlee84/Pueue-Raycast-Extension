default:
    @just --list

# run in Raycast dev mode (registers the extension; persists after you stop)
dev:
    ([ -d node_modules ] || npm install) && npm run dev

# typecheck + bundle (regenerates raycast-env.d.ts; does NOT install into Raycast)
build:
    ([ -d node_modules ] || npm install) && npm run build

lint:
    ([ -d node_modules ] || npm install) && npm run lint

fix:
    ([ -d node_modules ] || npm install) && npm run fix-lint

# real typecheck. `ray build` bundles but does NOT typecheck — it happily
# shipped a State/GroupMap mismatch and a ReactNode clash.
typecheck:
    ([ -d node_modules ] || npm install) && npx tsc --noEmit -p tsconfig.json

# assert the pure normalizers against src/lib/fixtures/state.json
verify:
    ([ -d node_modules ] || npm install) && rm -rf .build/verify && \
      npx tsc --outDir .build/verify --module commonjs --target ES2022 --lib ES2023 \
        --esModuleInterop --resolveJsonModule --skipLibCheck --strict src/lib/dev-check.ts && \
      node .build/verify/dev-check.js

# the pre-commit gate
check: typecheck verify lint build

# prove the committed lockfile still works from scratch
ci:
    rm -rf node_modules && npm ci && just check

# `npm run build` is bare `ray build`, which defaults to -e dev, so this path is
# otherwise never exercised.
# the build the store actually produces
dist:
    ([ -d node_modules ] || npm install) && npx ray build -e dist

# Verified: `ray lint` exits 0 with an empty metadata/. The checker lives in the
# raycast-extension-dev skill so it stays generic across extensions.
# store preconditions `ray lint` does NOT check
preflight:
    #!/usr/bin/env bash
    set -euo pipefail
    script=.agents/skills/raycast-extension-dev/scripts/check-store-readiness.sh
    if [ ! -f "$script" ]; then
      echo "missing $script" >&2
      echo "vendor it first:" >&2
      echo "  npx skills@latest add daviddwlee84/agent-skills/skills --skill raycast-extension-dev" >&2
      exit 3
    fi
    bash "$script" .

# The capture itself CANNOT be automated — Window Capture needs a hotkey and a
# ticked "Save to Metadata" — so this seeds the queue and walks the commands in
# order, leaving you only the keypress.
# stage a store screenshot session
shots:
    #!/usr/bin/env bash
    set -euo pipefail
    base="raycast://extensions/da-wei_lee/pueue"
    echo "Before you start:"
    echo "  - bind Window Capture (Raycast Settings -> Advanced), e.g. cmd-shift-opt-M"
    echo "  - run 'just dev' in another terminal so the extension is registered"
    echo "  - tick 'Save to Metadata' on the FIRST capture; it sticks after that"
    echo "  - a deeplink shows a 'triggered from outside Raycast' prompt: accept it,"
    echo "    THEN capture, or the prompt lands in the shot"
    echo
    just fixtures
    echo
    for shot in \
      "tasks|the list with running, stashed and failed rows" \
      "tasks|select the failed task and open the detail pane (cmd-shift-D)" \
      "add-task|reveal the advanced options with cmd-shift-A" \
      "groups|two or three groups with different parallelism"; do
      cmd="${shot%%|*}"; hint="${shot#*|}"
      printf '\n=> %s\n   %s\n   [enter] to open, or [s] to skip: ' "$cmd" "$hint"
      read -r key </dev/tty
      [ "$key" = "s" ] && continue
      open "$base/$cmd"
      printf '   capture it, then [enter] to continue: '
      read -r _ </dev/tty
    done
    echo
    echo "=> queue-menu — click the menu bar icon and capture. A menu-bar command"
    echo "   cannot be opened by deeplink, so this last one is entirely manual."
    echo
    echo "then: just preflight"

# `ray publish` copies the DIRECTORY, not the git index, so docs/, site/, .venv/
# and .specstory/ would otherwise ride along into the raycast/extensions PR.
# Allowlist, not denylist.
# an allowlisted copy of just the extension, for review
store-export:
    #!/usr/bin/env bash
    set -euo pipefail
    rm -rf .build/store && mkdir -p .build/store
    rsync -a --relative \
      package.json package-lock.json tsconfig.json eslint.config.mjs \
      raycast-env.d.ts src assets metadata README.md CHANGELOG.md LICENSE \
      .build/store/
    cd .build/store
    # `npm ci` here does double duty: it gives the export its own tsc (which
    # `ray build -e dist` needs), and it proves the committed lockfile installs
    # from a clean checkout, which is on the store checklist.
    npm ci
    npx ray lint
    npx ray build -e dist
    echo
    echo "clean copy in .build/store — it installs, lints, and dist-builds alone."
    echo "Publishing from a non-git temp dir is UNVERIFIED; if 'ray publish'"
    echo "refuses there, publish from the repo root and accept the ride-along."

# seed a local queue covering several status variants (needs a running daemon)
fixtures:
    pueue add 'sleep 120'
    pueue add -s 'echo stashed'
    pueue add 'false'
    pueue add 'echo ok'
    pueue add 'definitely-not-a-real-binary'
    pueue status
