# UI patterns

Read this when building a List/Detail/Form surface, adding a dropdown or keyboard
shortcut, rendering program output as markdown, or designing the empty and error
states.

## Table of contents

- [List](#list)
- [Client-side search via keywords](#client-side-search-via-keywords)
- [Accessories adapt to available width](#accessories-adapt-to-available-width)
- [Dropdowns](#dropdowns)
- [Detail and metadata](#detail-and-metadata)
- [ActionPanel and shortcuts](#actionpanel-and-shortcuts)
- [Confirmations and toasts](#confirmations-and-toasts)
- [Rendering untrusted output](#rendering-untrusted-output)
- [React types](#react-types)
- [Error states: one descriptor, N renderers](#error-states-one-descriptor-n-renderers)

---

## List

```tsx
<List
  isLoading={isLoading}
  isShowingDetail={showDetail && items.length > 0}
  onSelectionChange={setSelectedId}
  searchBarPlaceholder={`Search ${items.length} item${items.length === 1 ? "" : "s"}…`}
  searchBarAccessory={<GroupDropdown … />}
>
  <List.Section title="Running" subtitle={String(running.length)}>
    {running.map((t) => <Row key={t.id} … />)}
  </List.Section>
</List>
```

- `isShowingDetail` gated on a non-empty list, or an empty list renders a
  half-width nothing.
- `List.EmptyView` **must be rendered inside a `<List>`** — it is not standalone.
- Only one `searchBarAccessory` is allowed. Choose the dimension the user filters
  by most.
- Section order should be semantic (Running, Queued, Failed, Done), not
  alphabetical.

## Client-side search via keywords

If the whole dataset is already in memory, filter in the client through per-item
`keywords` rather than round-tripping a query to the backend. It is faster and
usually broader — a backend query DSL often matches only one or two fields.

```tsx
<List.Item keywords={searchKeywords(item)} … />
```

```ts
export function searchKeywords(t: Task): string[] {
  return [String(t.id), t.command, t.label, t.group, t.path, statusKind(t), resultKind(t)]
    .filter((k) => k && k.length > 0);          // drop nulls; never emit ""
}
```

Assert the filtering, including that a null field drops rather than emitting an
empty string that matches everything.

## Accessories adapt to available width

With the detail pane open, the list column is roughly a third of the window and
long accessories truncate into nonsense (`Pueu…  Showing…  Not…ected`). Render
fewer of them:

```tsx
const accessories: List.Item.Accessory[] = showDetail
  ? [{ tag: { value: statusTag(t), color: statusColor(t) } }]
  : [labelTag, groupText, { text: duration, icon: Icon.Clock }, statusTag];
```

The same reasoning is why an error descriptor needs a `shortTitle` as well as a
`title`.

## Dropdowns

Two non-obvious behaviours, both of which produce a silently wrong UI:

**Raycast silently resets a dropdown whose `value` is not among its children.**
If the persisted selection refers to something that has since been deleted, the
filter quietly reverts to the first item and the user sees a different dataset
than they asked for. Render a synthetic item so the value always exists:

```tsx
{!known.includes(value) && value !== ALL ? (
  <List.Dropdown.Item title={`${value} (gone)`} value={value} />
) : null}
```

**An empty dropdown looks broken.** Seed a static fallback list so the first
paint, before data arrives, is never empty.

Use a sentinel that cannot collide with real data:

```ts
export const ALL_GROUPS = " all";   // a leading space is not a legal group name
```

## Detail and metadata

```tsx
<List.Item.Detail
  markdown={md}
  metadata={
    <List.Item.Detail.Metadata>
      <List.Item.Detail.Metadata.Label title="Group" text={t.group} />
      <List.Item.Detail.Metadata.Separator />
      <List.Item.Detail.Metadata.TagList title="Status">
        <List.Item.Detail.Metadata.TagList.Item text={kind} color={color} />
      </List.Item.Detail.Metadata.TagList>
    </List.Item.Detail.Metadata>
  }
/>
```

Conditional rows are `{cond ? <Row/> : null}` — an undefined `text` renders an
empty row rather than nothing.

A standalone `<Detail>` takes `isLoading`, `navigationTitle`, `markdown`,
`metadata`, and `actions`, and is the right surface for a full error page or a
log view.

## ActionPanel and shortcuts

```tsx
<ActionPanel>
  <ActionPanel.Section>
    <Action.Push title="Show Log" target={<LogView id={t.id} />} />
    <Action title="Kill" style={Action.Style.Destructive}
            shortcut={{ modifiers: ["cmd", "shift"], key: "k" }} onAction={kill} />
  </ActionPanel.Section>
  <ActionPanel.Section title="Copy">
    <Action.CopyToClipboard content={t.command} shortcut={Keyboard.Shortcut.Common.Copy} />
  </ActionPanel.Section>
</ActionPanel>
```

- The **first action is the ⏎ action.** Order matters more than any other UI
  decision in the panel.
- Prefer `Keyboard.Shortcut.Common.*` (`Refresh`, `Copy`, `Open`, `Remove`,
  `New`, `Duplicate`) so bindings match the rest of Raycast.
- **`⌘K` and `⌘P` are reserved** (Open Action Panel, Open Search Bar Dropdown).
  Bind them and they are silently ignored — nothing throws. Only `ray lint`
  catches this.
- **Name a shortcut after what it does, not after its family.** If you have two
  restarts, one destructive and one not, `⌘⇧R` and `⌘⌥R` need to be documented by
  behaviour or someone will lose work.
- `ActionPanel.Submenu` for a set of alternatives (accounts, groups) that would
  otherwise flood the panel.

## Confirmations and toasts

```ts
if (needsConfirm && prefs.confirmDestructive) {
  const ok = await confirmAlert({
    title: "Remove 3 items?",
    message: "This cannot be undone.",
    rememberUserChoice: true,                     // "Do not show this again"
    primaryAction: { title: "Remove", style: Alert.ActionStyle.Destructive },
  });
  if (!ok) return false;
}
const toast = await showToast({ style: Toast.Style.Animated, title: "Removing…" });
try { await run(); toast.style = Toast.Style.Success; toast.title = "Removed"; }
catch (e) { await showFailureToast(e, { title: "Removing", message: firstLine(e.detail) }); }
```

- **Present tense for the in-flight title, past tense for the done title.**
- **State what the verb does not say.** If `kill --group` also pauses the group,
  or `remove` moves members to a default, the confirmation is where that goes —
  verified from the tool's `--help`, not guessed.
- **Omit `rememberUserChoice` on the genuinely irreversible one.** Some actions
  should ask every time.
- **On failure, surface the backend's own prose.** A good CLI refuses things for
  reasons better than anything you would write.
- `confirmAlert` **does not work from a menu-bar command** — see `menu-bar.md`.

## Rendering untrusted output

Program output is not markdown. A build log full of `#` and `*` renders as
headings and bullets, and a fence inside the output closes yours early.

```ts
function fence(text: string): string {
  // A ``` inside the output would close our fence; break it with a zero-width space.
  return "```text\n" + text.replace(/```/g, "``​`") + "\n```";
}
```

Use the same helper for stderr in an error page, log previews, and anything a
user typed.

## React types

**`@raycast/api` bundles its own copy of `@types/react`.** The root
`React.ReactNode` is therefore a structurally different type that silently fails
to match Raycast components' children. When a prop holds JSX, type it as an
element:

```ts
function logActions(t: Task, extra?: React.JSX.Element | null) { … }
```

`tsc` catches this; `ray build` does not.

## Error states: one descriptor, N renderers

The failure mode this avoids is real: an extension grew two copies of an
`errorMarkdown()` helper that drifted apart, because the menu bar cannot render a
`List.EmptyView` and needed its own.

```ts
export interface ErrorAction {
  id: string; title: string; icon?: Image.ImageLike;
  copy?: string; url?: string; run?: () => void | Promise<void>;
}
export interface ErrorDescriptor {
  icon: Image.ImageLike;
  title: string;
  shortTitle: string;     // a few words — the list column is a third of the window
  description: string;    // one line, for List.EmptyView
  markdown: string;       // a full page, for Detail
  actions: ErrorAction[];
  structural: boolean;
}
export function describeError(error: unknown): ErrorDescriptor { … }
```

**Actions are data, not JSX**, so each renderer maps them to its own primitive:

```tsx
// In an ActionPanel
a.copy !== undefined ? <Action.CopyToClipboard content={a.copy} />
: a.url !== undefined ? <Action.OpenInBrowser url={a.url} />
: <Action title={a.title} onAction={() => a.run?.()} />

// In a MenuBarExtra
onAction={() => { if (a.copy) Clipboard.copy(a.copy); else if (a.url) open(a.url); else a.run?.(); }}
```

Content that earns its place in `markdown`:

- The exact install/start commands, as copy-to-clipboard actions **and** in the
  text.
- `openExtensionPreferences` as an action on every structural error.
- The raw stderr in a fence.
- An explanation of what each distinct backend error string actually means.
- Context sensitivity: never offer to start something you are not talking to.
  If the failing target is a remote host, a local "start service" action is
  actively misleading.
