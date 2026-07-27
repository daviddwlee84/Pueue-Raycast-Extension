# `ray lint` passes locally and fails in CI

## Symptoms (grep this section)

```text
error  - validate package-lock.json
        error  Found references to a non-official npm registry
               (registry.npmmirror.com). Remove them
```

…from a CI job, on a commit whose `just lint` was green on the machine that
wrote it.

## Cause

**`ray lint` runs more checks when it believes it is in CI.** Locally the output
is five steps:

```text
ready  - validate package.json file
ready  - validate extension icons
ready  - validate extension metadata
ready  - run ESLint
ready  - run Prettier
```

With `CI=true` it is seven — two lockfile validations appear that are simply
absent otherwise:

```text
ready  - validate package.json file
ready  - validate package-lock.json      ← only under CI
ready  - validate other lock files       ← only under CI
ready  - validate extension icons
…
```

So the local gate is **weaker** than the remote one, which is the opposite of
what everyone assumes a gate does. Nothing announces the difference; the missing
steps just aren't printed.

The specific check that fires: the store requires every `resolved` URL in
`package-lock.json` to point at `registry.npmjs.org`. If your global npm registry
is a mirror — `registry.npmmirror.com`, an internal Artifactory, anything — then
`npm install` writes that host into the lockfile, and every one of those URLs is
a lint error.

This repo shipped 215 of them and the extension workflow failed on **every push
since it was added**, including the commit that added it.

## Fix

Two parts, and the second is the one that lasts.

**Rewrite the lockfile.** The tarballs are byte-identical across mirrors, so the
`integrity` hashes stay valid and a plain host substitution is safe:

```sh
sed -i '' 's|registry\.npmmirror\.com|registry.npmjs.org|g' package-lock.json
```

`npm install --package-lock-only --registry=…` does **not** do this — npm keeps
the `resolved` URLs already in the file. Prove the result installs:

```sh
mkdir /tmp/proof && cp package.json package-lock.json /tmp/proof/
cd /tmp/proof && npm ci --registry=https://registry.npmjs.org/
```

**Make the local gate match CI.** Otherwise it comes back the next time anyone
runs `npm install`:

```make
lint:
    ([ -d node_modules ] || npm install) && CI=true npm run lint
```

For a permanent fix on a machine whose global registry is a mirror, add an
`.npmrc` to the repo:

```ini
registry=https://registry.npmjs.org/
```

That pins resolution per-project. It is deliberately **not** committed here,
because forcing a slower or unreachable registry on the person doing the work is
a real cost, and `CI=true` in the lint recipe now catches the drift locally
before it reaches a push.

## The general shape

Any tool that behaves differently under `CI=true` turns your local gate into a
subset of the real one. When a build fails on something that passed locally,
check for an environment-conditional code path before assuming the runner is
different in some deeper way — and then pin the environment in the local recipe
so the two cannot diverge again.

## Related

[ray-build-does-not-typecheck.md](ray-build-does-not-typecheck.md) — the same
lesson from the other direction: `ray build` in its default `dev` environment
skips the typecheck that `-e dist` runs. Two commands, two environments, two
different sets of checks, and neither says which one you are getting.
