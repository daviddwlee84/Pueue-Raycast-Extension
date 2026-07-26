# Publishing to the Raycast Store

Read this when the user asks to publish, submit, or "make this store-ready", or
when `check-store-readiness.sh` reports a failure you want the reasoning behind.

## Table of contents

- [What publishing actually is](#what-publishing-actually-is)
- [The checklist](#the-checklist)
- [Screenshots](#screenshots)
- [What ray lint does and does not check](#what-ray-lint-does-and-does-not-check)
- [The CHANGELOG placeholder](#the-changelog-placeholder)
- [Requiring an external binary](#requiring-an-external-binary)
- [What rides along](#what-rides-along)
- [What cannot be automated](#what-cannot-be-automated)

---

## What publishing actually is

`npx @raycast/api@latest publish` authenticates with GitHub and opens a **pull
request against `raycast/extensions`**. CI runs on the PR, then a human from the
Raycast team reviews it. Turnaround is days, not seconds.

This is closer to Homebrew core than to npm. Treat submission as a one-way door:
the readiness checks below are the validation step, and the publish command is
something the user types, not something a script fires.

`ray publish` has no `--dry-run`, no `--yes`, and no path argument. Its
`-I/--non-interactive` flag only suppresses interactive *output*; it does not
supply credentials or answers.

## The checklist

```text
[ ] author is your REGISTERED Raycast username (not your display name)
[ ] license: "MIT" in package.json AND an MIT LICENSE file at the root
[ ] at least one category, Title Case, from Raycast's list
[ ] platforms: ["macOS"] if any command is mode: "menu-bar"
[ ] a one-sentence description — this is what the store shows
[ ] package-lock.json committed; npm ci must work from a clean checkout
[ ] assets/extension-icon.png at exactly 512x512, readable on light AND dark
[ ] a separate monochrome template SVG for any menu-bar icon
[ ] CHANGELOG.md opening with `## [Initial Version] - {PR_MERGE_DATE}`
[ ] README.md covering setup, external requirements, and any default that
    makes a fresh install look broken
[ ] 3-6 PNGs at exactly 2000x1250 in metadata/
[ ] no `version` field in package.json — the store derives it
[ ] no Keychain access (extensions requesting it are rejected)
[ ] tsc --noEmit clean, ray lint clean, ray build -e dist clean
```

Note the last one. `ray build` defaults to `-e dev`; `npm run build` is usually
bare `ray build`, so **the distribution build may never have been run**. Run
`ray build -e dist` at least once before submitting — and note it is a stronger
check than the dev build: it shells out to `tsc -p tsconfig.json --noEmit`, so it
fails on type errors the dev build cheerfully bundles.

Naming follows the Apple Style Guide: Title Case for extension and command
titles, US English throughout, specific rather than generic names.

## Screenshots

Spec: **3–6 PNGs, exactly 2000×1250 (16:10)**, in `metadata/`. No dark-mode
variants — one set only.

The supported capture path is Raycast's **Window Capture**:

1. Give it a hotkey: Raycast Settings → Advanced → Window Capture, or bind the
   **Capture Window** command.
2. Open the extension command you want to shoot, under `ray develop`.
3. Press the hotkey, **tick "Save to Metadata"**, click the camera button.

**"Save to Metadata" only appears when a `metadata/` folder already exists.**
Create it before you start — put a README inside so git tracks it
(`assets/metadata-README.md.template` is exactly that file).

Guidance from the store docs: one consistent background, good contrast,
informative commands rather than empty states, no sensitive data, no other apps,
and do not mix light and dark unless the theme is the point.

Seed real data first. A screenshot of an empty list sells nothing, and a fixture
recipe (`just fixtures`) that puts the backend into a state covering every status
variant is worth writing before you capture anything.

## What ray lint does and does not check

`ray lint` runs five stages: validate `package.json` against the schema, validate
extension icons, validate extension metadata, ESLint, Prettier.

Verified behaviour that matters: **`ray lint` exits 0 with a completely empty
`metadata/` directory.** "Validate extension metadata" does not mean "you have
screenshots". So the linter cannot be your gate for submission readiness.

| Checked by `ray lint` | Not checked by anything until review |
| --- | --- |
| manifest schema, including the preference-type union | screenshot count |
| icon present and valid | screenshot dimensions |
| ESLint + Prettier | icon is not still the default/placeholder |
| reserved-shortcut collisions | CHANGELOG placeholder present |
| | `author` is a *registered* username |
| | `npm ci` works from a clean checkout |
| | types (that is `tsc`, separately) |

`scripts/check-store-readiness.sh` covers the right-hand column. `ray lint`
`--relaxed` skips package.json/icon/metadata validation entirely — useful during
development, never before submitting.

## The CHANGELOG placeholder

```markdown
# My Extension Changelog

## [Initial Version] - {PR_MERGE_DATE}

- What the first release does.
```

`{PR_MERGE_DATE}` is a literal placeholder that Raycast's tooling substitutes on
merge. **Do not fill it in yourself.**

For a first submission, ship **one** `{PR_MERGE_DATE}` section. Multiple
unreleased sections on an extension that has never shipped is a reviewer nit with
no upside — nothing was ever released, so there is nothing to preserve by keeping
them separate. Fold them into `[Initial Version]`.

## Requiring an external binary

The store guidance says:

> "Avoid asking users to perform additional downloads and try to automate as much
> as possible from the extension, especially if you are targeting non-developers."

and, separately, explicitly allows:

> "✅ Calling known system binaries"

A Homebrew-installed CLI is the second. The first is soft ("try to",
"especially if…") and there is shipping precedent for requiring a pre-installed
CLI or daemon: **Homebrew**, **Colima**, **OrbStack**, **Yabai**.

What a reviewer actually looks for is **graceful degradation** — not zero
dependencies. An extension that renders a red error object when its binary is
missing reads as broken. One that renders "X was not found", explains why
(launchd strips `PATH`), and offers copy-to-clipboard install commands plus a
link to extension preferences, reads as finished. Build that first; it is also
the thing your users need most.

Do **not** bundle an opaque binary, and do not download one at runtime without an
integrity check.

## What rides along

`ray publish` copies the **extension directory verbatim** into
`extensions/<name>/` of the PR — and the extension directory is the repo root,
because `ray build`/`develop`/`lint` all resolve `./package.json`.

It copies the directory, **not the git index**, so `.gitignore` does not help.
Notes, task lists, a docs site, a virtualenv, a `site/` build output, agent
transcripts — all of it lands in the review PR.

If that matters, export an allowlisted subset first:

```bash
rsync -a --delete \
  package.json package-lock.json tsconfig.json eslint.config.mjs raycast-env.d.ts \
  src assets metadata README.md CHANGELOG.md LICENSE \
  .build/store/
cd .build/store && npx ray lint && npx ray build -e dist
```

Running the gate in the export is the point: it proves the subset is
self-sufficient before anyone reviews it.

## What cannot be automated

Three steps are human-in-the-loop by construction. Say so plainly rather than
building something brittle:

1. **Screenshots.** Window Capture needs a GUI hotkey and a ticked checkbox.
   Community extensions that wrap `screencapture` exist, but they are
   resolution-dependent — one documents that it only produces correct dimensions
   when the display's actual resolution is exactly twice its UI scaling.
   `screencapture` itself photographs the desktop, not the Raycast window.
2. **`ray login`** opens a browser OAuth flow. (`ray token` will print an existing
   token afterwards, but there is no documented way to inject one.)
3. **`ray publish`** opens a PR that a human reviews.

What *can* be automated, and mostly is not by default: `ray build -e dist`, a
clean-lockfile proof (`rm -rf node_modules && npm ci && <gate>`), the allowlisted
export above, `check-store-readiness.sh`, and CI running the gate on every push.
Sequencing the screenshot session — seeding fixtures and opening each command by
deeplink in order — is worth scripting too, even though the capture itself is not.
