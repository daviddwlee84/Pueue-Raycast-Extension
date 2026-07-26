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

# seed a local queue covering several status variants (needs a running daemon)
fixtures:
    pueue add 'sleep 120'
    pueue add -s 'echo stashed'
    pueue add 'false'
    pueue add 'echo ok'
    pueue add 'definitely-not-a-real-binary'
    pueue status
