# Raycast surfaces, as they exist in 2026

Why this extension is shaped the way it is. Written after checking the live
docs, because the answer to "can Raycast do X" is frequently no in a way that is
hard to confirm from search results.

## There is no widget API

The complete list of extension entry points:

| Entry point | Manifest | Notes |
| --- | --- | --- |
| View command | `"mode": "view"` | pushes a view onto the navigation stack |
| No-view command | `"mode": "no-view"` | runs and exits, no UI |
| **Menu bar command** | `"mode": "menu-bar"` | returns a `MenuBarExtra`. **macOS only** |
| AI tools | `"tools": [...]` | invoked by Raycast AI, **Pro-gated** |
| Script commands | separate repo | shell scripts, not TS extensions |

That's it. No desktop widget surface, no Notification Center or Control Center
extension point, no Live-Activity equivalent, no floating window API. Nothing
widget-shaped shipped for extensions in "The New Raycast" (May 2026) or Glaze
(July 2026), and the "Widgets & Controls" pages on raycast.com are **iOS only**.

**So a `mode: "menu-bar"` command is the only way to show something without
opening Raycast**, and that is what `src/queue-menu.tsx` is.

## Menu bar constraints

### There is no badge API

The count *is* the `title`, and `undefined` removes it. Verified live: with
nothing running the number disappears and only the glyph remains.

```tsx
title={running > 0 ? String(running) : undefined}
```

This is the same idiom Homebrew's `services-menu` uses — the closest structural
precedent in the store, and worth reading.

### `isLoading` is a contract

From the docs, marked "danger": either never set it — in which case Raycast
renders and **immediately unloads** — or set it `true` during async work and
`false` when done. For a `menu-bar` command the whole React tree re-runs on
every tick until it goes false.

### `interval` is manifest-only

A preference cannot change it. Raycast renders its own refresh-interval control
in the command's settings. Brew hardcodes `"interval": "1m"` and ships no
interval preference; shipping one would be a lie.

The docs contradict themselves on the floor — the background-refresh page says
`10s`, the manifest page says `1m`. `1m` is the safe value.

Verified live: with three tasks started and **no** trigger from the extension,
the menu bar count updated on its own within 75 seconds.

### Background refresh is off by default for store installs

Until the user runs the command once or enables it in settings, a freshly
installed menu bar command shows **nothing**. This is the single most likely
"it's broken" report, so it's near the top of the README.

### Restart restores from a database, not by re-running

A stale render can outlive a Raycast restart, and there are open upstream issues
about stuck menu bar icons. The mitigation here is the `Updated HH:MM` row —
it makes staleness visible rather than misleading.

### `confirmAlert` is unavailable in practice

It presents in the Raycast window, which is closed when the menu is open. Rather
than risk a silently swallowed confirmation on a destructive action, destructive
menu bar items sit behind `⌥` and `Reset` / `Remove Group` aren't offered there
at all.

### Identical sibling items misfire

The docs warn that two identical `MenuBarExtra.Item`s at the same level get
their `onAction` handlers crossed. Every task row is prefixed with its id, which
makes collisions impossible.

## Deeplinks prompt when triggered externally

`open "raycast://extensions/<author>/<ext>/<command>"` shows:

> **Request to open ‹Command›** — The command was triggered from outside of
> Raycast. If you did not do this, please cancel the operation.

Per-command trust. It matters for two reasons: it's why an external nudge is a
poor substitute for `launchCommand` (see
[`../backlog/callback-notifications.md`](https://github.com/daviddwlee84/Pueue-Raycast-Extension/blob/main/backlog/callback-notifications.md)),
and it's why some verification during development needs a real keypress rather
than a deeplink.

`launchCommand` **from inside** an extension does not prompt, and
`LaunchType.Background` is the documented way to force a sibling command's
refresh — which is what every mutation does to the menu bar.

## Reserved shortcuts

`⌘K` (OpenActionPanel) and `⌘P` (OpenSearchBarDropdown) are Raycast's own. Bind
them and they are **silently ignored**. `ray lint` catches this; `tsc` does not.

## What `ray` actually checks

| Command | Checks |
| --- | --- |
| `ray build` | bundles with esbuild — **does not typecheck** |
| `ray lint` | ESLint + Prettier + manifest + icons — does not typecheck |
| `tsc --noEmit` | types |

All three are needed. See
[`../pitfalls/ray-build-does-not-typecheck.md`](https://github.com/daviddwlee84/Pueue-Raycast-Extension/blob/main/pitfalls/ray-build-does-not-typecheck.md).
