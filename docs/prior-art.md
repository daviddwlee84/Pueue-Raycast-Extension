# Prior art

Extensions in the Raycast Store that solve the same shape of problem this one
does: **wrap a CLI or daemon the user installed themselves, and make its state
visible without opening a terminal.** Collected because each one settles a
question that is otherwise a matter of opinion.

## Extensions that require a pre-installed binary

The store guidance says "avoid asking users to perform additional downloads",
and separately allows "✅ Calling known system binaries". These four are the
evidence that the second clause is real:

| Extension | Requires | What it is precedent for |
| --- | --- | --- |
| [Brew](https://www.raycast.com/nhojb/brew) | `brew` | The closest structural precedent. Its `services-menu` command is a `menu-bar` command at `interval: 1m` (checked against [its manifest](https://github.com/raycast/extensions/blob/main/extensions/brew/package.json)), and the title-as-badge idiom in [`raycast-surfaces.md`](raycast-surfaces.md) came from reading it. |
| [Colima](https://www.raycast.com/MiskaMyasa/colima) | `colima`, Docker CLI | A container runtime installed via Homebrew, driven entirely by shelling out. |
| [OrbStack](https://www.raycast.com/nicholasq/orbstack) | `orbctl` / `orb` | Same, for a commercial app's CLI. |
| [Yabai](https://www.raycast.com/krzysztoff1/yabai) | `yabai` | Requires not just a binary but a *running daemon* — and documents a `yabai` signal that pokes a Raycast background command to refresh a menu bar indicator. |

What a reviewer looks for is not zero dependencies but **graceful degradation**:
an extension that renders a raw error object when its binary is missing reads as
broken; one that explains the problem and offers the install command reads as
finished. That is what [`src/lib/error-states.tsx`](https://github.com/daviddwlee84/Pueue-Raycast-Extension/blob/main/src/lib/error-states.tsx)
is for.

## macOS job and service management

| Extension | Coverage | Note |
| --- | --- | --- |
| [Launch Agents](https://www.raycast.com/stevensd2m/launch-agents) | list, load, unload, remove `~/Library/LaunchAgents` entries — 1,214 installs | The launchd niche as it stands. It covers management, not observability: no exit status, no next-fire-time, no log tail. See [`backlog/launchd-jobs-extension.md`](https://github.com/daviddwlee84/Pueue-Raycast-Extension/blob/main/backlog/launchd-jobs-extension.md) for why that gap is the interesting part, and why closing it is harder than it looks. |

For the broader "which tool for which job" question — `pueue` vs
`systemd-run --user` vs `nohup` vs `tmux` — see
[this comparison note](https://gist.github.com/daviddwlee84/35a2aa5d477c99c8615a6232f1a1f308).

## Extension-development tooling

| Extension | What it does | Why it is listed |
| --- | --- | --- |
| [Capture Raycast Metadata](https://www.raycast.com/koinzhang/capture-raycast-metadata) | Screenshots the Raycast window for `metadata/` — 819 installs | A worked example of **why store screenshots are not automatable.** It drives the ScreenCapture app and only produces correct dimensions when the display's actual resolution is exactly twice its UI scaling; its own page says it "only works properly in some resolutions". Raycast's built-in Window Capture, with "Save to Metadata" ticked, remains the only reliable path. |

## Reference material

- [Prepare an Extension for Store](https://developers.raycast.com/basics/prepare-an-extension-for-store)
  — the checklist, and the source of the 2000×1250 screenshot spec.
- [Menu Bar Commands](https://developers.raycast.com/api-reference/menu-bar-commands)
  — including the `isLoading` "danger" note that [`raycast-surfaces.md`](raycast-surfaces.md)
  quotes.
- [raycast/extensions](https://github.com/raycast/extensions) — every published
  extension's source. Reading a shipped extension that solves an adjacent problem
  is consistently faster than searching the docs for whether something is possible.
- The generalised version of everything learned here now lives in the
  [`raycast-extension-dev`](https://daviddwlee84.github.io/agent-skills/skills/raycast-extension-dev/)
  agent skill.
