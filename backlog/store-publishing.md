# Publishing to the public Raycast store

**Status:** `P?` · decision deferred by design

## Where things stand

The repo is built store-ready. Everything on Raycast's checklist is done except
screenshots:

- [x] `author` is a registered Raycast username (`da-wei_lee`), MIT `LICENSE`,
      `license` field, categories, one-sentence description, `platforms`
- [x] committed `package-lock.json` (`npm ci` works from a clean checkout)
- [x] 512×512 PNG icon that reads on light and dark
- [x] `CHANGELOG.md` opening with `## [Initial Version] - {PR_MERGE_DATE}`
- [x] `README.md` covering install, daemon start, and the background-refresh
      default
- [x] `ray lint` and `ray build` clean, plus `tsc --noEmit`
- [x] graceful onboarding for both structural failures
- [ ] **3–6 screenshots at 2000×1250**, captured with Raycast's *Save Screenshot*

## The reviewable question

Store guidance says:

> "Avoid asking users to perform additional downloads and try to automate as
> much as possible from the extension, especially if you are targeting
> non-developers."

but also explicitly allows:

> "✅ Calling known system binaries"

A Homebrew-installed `pueue` is the second. The guidance is soft ("try to",
"especially if…") and there is shipping precedent for requiring a pre-installed
CLI or daemon: **brew** (which itself ships a `menu-bar` command at
`interval: 1m`), **colima**, **orbstack**, and **yabai**.

What a reviewer looks for is graceful degradation rather than a raw error, which
is what `src/lib/error-states.tsx` is.

## Publishing mechanics

`npm run publish` (= `npx @raycast/api@latest publish`) authenticates with
GitHub and opens a pull request against `raycast/extensions`. CI runs, then the
Raycast team reviews. Turnaround is days, not seconds. This is closer to
Homebrew core than to PyPI.

## One wrinkle

`npm run publish` uploads the extension directory, which here is the repo root —
so `backlog/`, `pitfalls/`, `TODO.md`, and the `Justfile` would ride along and
add review noise. They're small and harmless, and a reviewer is unlikely to
care.

If one does, the escape hatch is a `just store-export` recipe that rsyncs a
clean subset to a temp directory and publishes from there. Not worth building
pre-emptively.

## What would have to be true

Screenshots taken, and a decision that this should be public rather than
personal. Local `ray develop` already persists the extension in root search
after dev stops, so nothing is blocked on publishing.
