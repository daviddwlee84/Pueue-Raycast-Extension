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
- [x] `just dist` (`ray build -e dist`) clean — the build the store actually
      produces, and the one that also typechecks
- [x] `just store-export` proves an allowlisted subset installs from the
      committed lockfile, lints, and dist-builds on its own
- [ ] **3–6 screenshots at 2000×1250**, captured with Raycast's *Window Capture*

Run `just preflight` for a machine-checked version of this list.

## What the toolchain does and does not enforce

Measured, not assumed:

| Claim | How it was checked |
| --- | --- |
| **`ray lint` exits 0 with `metadata/` empty** | ran it here, today — all five stages green |
| `ray build` (default `-e dev`) exits 0 on a genuine `TS2345` | scratch extension carrying one type error |
| `ray build -e dist` exits 1 on the same file | it shells out to `tsc -p tsconfig.json --noEmit` |
| `ray publish` has no `--dry-run`, no `--yes` | `ray publish --help`; `-I` only suppresses interactive *output* |
| this machine is not logged in | `ray profile` → "please first log in first using `npx ray login`" |

So the linter cannot be the submission gate. `just preflight` is — it runs
`check-store-readiness.sh` from the `raycast-extension-dev` skill, which checks
screenshot count and dimensions, icon size, whether the icon is still a
placeholder, the CHANGELOG placeholder, the lockfile, and a non-placeholder
`author`.

## What can and cannot be automated

Three steps are human-in-the-loop by construction:

1. **Screenshots** — Window Capture needs a GUI hotkey and a ticked
   "Save to Metadata". The community `Capture Raycast Metadata` extension is not
   a way around it: it only produces correct dimensions when the display's actual
   resolution is exactly twice its UI scaling. `screencapture` photographs the
   desktop, not the Raycast window.
2. **`ray login`** — a browser OAuth flow.
3. **`ray publish`** — opens a PR that a human reviews.

Everything else now is: `just dist`, `just store-export`, `just preflight`,
`just shots` (which seeds fixtures and walks the commands by deeplink so the only
manual act left is the keypress), and `.github/workflows/extension.yml` running
the gate on every push.

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
and it copies the **directory, not the git index**, so `.gitignore` does not
help. `backlog/`, `pitfalls/`, `TODO.md`, `Justfile`, `docs/`, `mkdocs.yml`,
`pyproject.toml`, `uv.lock`, `.specstory/`, `.agents/`, `.claude/`, and any
present-but-ignored `site/` or `.venv/` would all ride along.

That list has grown well past "small and harmless" since this note was written.
So the escape hatch got built: **`just store-export`** rsyncs an allowlisted
subset into `.build/store/`, runs `npm ci` there (which doubles as the
clean-checkout lockfile proof the store requires), then `ray lint` and
`ray build -e dist`. Verified: the subset stands on its own.

**Unverified:** whether `ray publish` will run from a non-git directory. Treat
`.build/store` as a verification copy first. If publish refuses there, publish
from the root and accept the ride-along — and record that here.

## What would have to be true

Screenshots taken, and a decision that this should be public rather than
personal. Local `ray develop` already persists the extension in root search
after dev stops, so nothing is blocked on publishing.
