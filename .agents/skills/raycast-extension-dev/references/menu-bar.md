# Menu bar commands

Read this when the extension has or is gaining a `mode: "menu-bar"` command, or
when the menu bar shows stale data, a wrong count, or nothing at all.

## Table of contents

- [Why it matters more than it looks](#why-it-matters-more-than-it-looks)
- [The manifest side](#the-manifest-side)
- [Title, icon, and loading](#title-icon-and-loading)
- [Items](#items)
- [What you cannot do here](#what-you-cannot-do-here)
- [Staleness](#staleness)
- [Errors](#errors)
- [Skeleton](#skeleton)

---

## Why it matters more than it looks

**There is no widget API.** No Notification Center extension point, no Control
Center surface, no Live-Activity equivalent, no floating window. A
`mode: "menu-bar"` command is the *only* way for an extension to show something
without the user opening Raycast.

That makes it the single most valuable surface for anything ambient — a count, a
status, a health indicator — and it is also the most constrained. Design around
the constraints rather than discovering them one at a time.

## The manifest side

```json
{ "name": "queue-menu", "title": "Queue Menu Bar", "mode": "menu-bar", "interval": "1m" }
```

- **`platforms: ["macOS"]` becomes mandatory** at the top level. Menu bar is
  macOS-only.
- **`interval` is manifest-only.** A preference cannot change it, because Raycast
  renders its own refresh-interval control in the command's settings. Shipping an
  interval preference would be a lie.
- The docs contradict themselves on the floor — the background-refresh page says
  `10s`, the manifest page says `1m`. **Use `1m`.** It is what shipped
  extensions use and it is the safe value.
- **Background refresh is off by default for store installs.** Until the user
  runs the command once or enables it in settings, a freshly installed menu-bar
  command shows *nothing*. This is the most likely "it's broken" report you will
  get, so it belongs near the top of the README.

## Title, icon, and loading

**There is no badge API.** The count *is* the `title`, and `undefined` is what
removes it:

```tsx
title={running > 0 ? String(running) : undefined}
```

With nothing to report, the number disappears and only the glyph remains. This is
the idiom shipped extensions use; there is no alternative.

For the icon, ship **one monochrome template SVG** (16×16, black shapes, no
colour) and vary only `tintColor`:

```ts
function menuIcon(s: Summary): Image.ImageLike {
  if (s.loading) return { source: "icon.svg", tintColor: Color.SecondaryText };
  if (s.failed > 0) return { source: "icon.svg", tintColor: Color.Red };
  if (s.allPaused) return { source: "icon.svg", tintColor: Color.Orange };
  return { source: "icon.svg", tintColor: Color.PrimaryText };
}
```

The shape never moves in the menu bar, only its colour — which reads at a glance
and does not jitter the neighbouring items.

**`isLoading` is a contract, not a hint.** The docs mark this "danger", and it is
accurate:

- Never set it → Raycast renders the item and **immediately unloads** it.
- Leave it stuck `true` → the whole React tree re-runs on every tick.

Set it `true` during async work and `false` when done. Passing the hook's own
`isLoading` through is usually correct.

## Items

```tsx
<MenuBarExtra.Section title="Running">
  <MenuBarExtra.Item
    title={`${t.id} · ${oneline(t.command, 48)}`}
    icon={statusIcon(t)}
    onAction={() => open(t)}
  />
</MenuBarExtra.Section>
```

- **A `MenuBarExtra.Item` with no `onAction` is a disabled label.** That is how
  you render a header row or a status line.
- **Two identical items at the same level cross their `onAction` handlers.**
  Raycast warns about this. Prefix every row with its id so collisions are
  impossible.
- **`alternate` is the ⌥ affordance.** The visible item names the modifier; the
  alternate does the work:

  ```tsx
  <MenuBarExtra.Item
    title="Hold ⌥ to Clean Finished"
    alternate={<MenuBarExtra.Item title="Clean Finished" onAction={clean} />}
  />
  ```

- **Cap the item count.** Read it from a preference with a sane default (7 per
  section) and render a trailing `…and 23 more` item that `launchCommand`s the
  full list view. A 400-row backend must not become 400 menu items every 60
  seconds.
- Feedback is **`showHUD`**, not `showToast`. There is no window to host a toast.
- `updateCommandMetadata({ subtitle })` inside the fetch's `onData` keeps this
  command's own root-search subtitle current. `.catch(() => {})` it.

## What you cannot do here

**`confirmAlert` cannot present.** It renders in the Raycast window, which is
closed while the menu is open. A silently swallowed confirmation on a destructive
action is not an acceptable outcome, so:

- Put destructive items behind `alternate` (⌥), which is itself a small
  deliberateness gate.
- Leave the genuinely dangerous ones (reset, delete-everything) out of the menu
  entirely. They belong in the view command where a confirmation works.

**Do not push views.** There is no navigation stack. Use `launchCommand` to open
a view command, passing the context it needs.

## Staleness

Raycast **restores a menu-bar item from its database on restart**, rather than by
re-running the command. So a render from before the restart can outlive it, and
there are open upstream reports of stuck menu-bar icons.

You cannot prevent this. You can make it visible:

```tsx
<MenuBarExtra.Item title={`Updated ${clock(fetchedAt)}`} />   {/* no onAction: a label */}
```

Store the timestamp **in the same cache entry as the data**, so the two cannot
drift:

```ts
export async function snapshot(o?: Options): Promise<Snapshot> {
  return { state: await read(o), fetchedAt: Date.now() };
}
```

Fetching the timestamp separately, or computing it at render time, produces a row
that says "Updated 10:42" above data from 09:15.

## Errors

**Never return `null`.** That removes the item from the menu bar entirely — the
worst possible answer for someone who deliberately enabled a status indicator,
because it looks identical to having uninstalled it.

Render an error menu instead, built from the same `ErrorDescriptor` the view
commands use (see `ui-patterns.md`):

```tsx
function ErrorMenu({ d }: { d: ErrorDescriptor }) {
  return (
    <MenuBarExtra icon={{ source: Icon.XMarkCircle, tintColor: Color.Red }} tooltip={d.title}>
      <MenuBarExtra.Item title={d.shortTitle} />
      <MenuBarExtra.Section>
        {d.actions.map((a) => (
          <MenuBarExtra.Item key={a.id} title={a.title} icon={a.icon}
            onAction={() => { if (a.copy) Clipboard.copy(a.copy);
                              else if (a.url) open(a.url); else a.run?.(); }} />
        ))}
      </MenuBarExtra.Section>
    </MenuBarExtra>
  );
}
```

## Skeleton

```tsx
export default function Command() {
  const prefs = getPreferenceValues<Preferences.QueueMenu>();
  const { data, isLoading, error } = useCachedPromise(snapshot, [], {
    keepPreviousData: true,
    onData: (d) => updateCommandMetadata({ subtitle: summarise(d) }).catch(() => {}),
  });

  if (error && !data) return <ErrorMenu d={describeError(error)} />;

  const s = summarise(data);
  return (
    <MenuBarExtra icon={menuIcon(s)} title={menuTitle(s, prefs.titleCounts)}
                  tooltip={s.tooltip} isLoading={isLoading}>
      <MenuBarExtra.Item title={`Updated ${clock(data?.fetchedAt)}`} />
      {/* sections, capped */}
      <MenuBarExtra.Section>
        <MenuBarExtra.Item title="Open Full List"
          onAction={() => launchCommand({ name: "tasks", type: LaunchType.UserInitiated })
            .catch(() => {})} />
      </MenuBarExtra.Section>
    </MenuBarExtra>
  );
}
```

After any mutation elsewhere in the extension, nudge this command with
`launchCommand({ name: "queue-menu", type: LaunchType.Background })` — but delay
it by the same reconcile interval the view uses, or it re-reads exactly the stale
state you just worked around. See `data-and-state.md`.
