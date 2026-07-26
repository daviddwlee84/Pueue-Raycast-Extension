# Manifest and commands

Read this when adding or renaming a command, adding preferences or arguments,
wiring one command to launch another, or considering AI tools.

## Table of contents

- [Top level](#top-level)
- [commands[]](#commands)
- [arguments[]](#arguments)
- [preferences[]](#preferences)
- [The generated types](#the-generated-types)
- [Cross-command launching](#cross-command-launching)
- [Deeplinks](#deeplinks)
- [AI tools](#ai-tools)

---

## Top level

```json
{
  "$schema": "https://www.raycast.com/schemas/extension.json",
  "name": "my-ext",
  "title": "My Extension",
  "description": "One sentence. This is what the store shows.",
  "icon": "extension-icon.png",
  "author": "your_raycast_username",
  "license": "MIT",
  "categories": ["Developer Tools", "Productivity"],
  "platforms": ["macOS"],
  "commands": [],
  "preferences": []
}
```

- `name` is the store slug and must be kebab-case. `title` is what users see.
- `author` must be your **registered Raycast username**, not your name.
- `icon` resolves relative to `assets/`.
- **`platforms: ["macOS"]` is mandatory when any command is `menu-bar`** — that
  mode does not exist elsewhere.
- **There is no `version` field** for a store extension. The store derives it.
- `$schema` gives you completion in an editor and is what `ray lint` validates
  against.

### String length minimums

The schema enforces minimums that are easy to trip and whose error message
(`must NOT have fewer than N characters`) **does not name the field** — you get a
line number and have to count. Read straight from
<https://www.raycast.com/schemas/extension.json>:

| Field | min | max |
| --- | ---: | ---: |
| `name` | 3 | 255 |
| `title` | 2 | 255 |
| `description` | **16** | 2048 |
| `author`, `owner`, `contributors[]` | 2 | 75 |
| `keywords[]` | 1 | 25 |
| `commands[].name` / `.title` / `.subtitle` | 2 | 255 |
| `commands[].description` | **12** | 2048 |
| `preferences[].name` | 2 | 255 |
| `preferences[].description` | **8** | 1024 |
| `preferences[].title` | 2 (0 for a checkbox) | 255 |
| `preferences[].label` (checkbox) | 1 | 255 |
| `arguments[].name` | 2 | 255 |
| `arguments[].placeholder` | 1 | 255 |
| `tools[].name` | 2 | 64 |
| `tools[].description` | **12** | 2048 |

"Tasks." is 6 characters and will be rejected. Write a sentence.

## commands[]

```json
{
  "name": "tasks",
  "title": "Tasks",
  "subtitle": "My Extension",
  "description": "Browse and act on the queue.",
  "mode": "view",
  "interval": "1m",
  "arguments": [],
  "preferences": []
}
```

**`commands[].name` must equal the filename**: `"name": "tasks"` requires
`src/tasks.tsx`. A mismatch is a build error with a message that does not say so
clearly.

Modes, and what each implies:

- **`view`** — default-exports a React component. Pushes onto the navigation
  stack. `useNavigation()` gives `push`/`pop`.
- **`no-view`** — default-exports `async function Command(props)`. **Not a
  component.** There is no window, so `showToast` degrades to a HUD; call
  `showHUD` directly to make that explicit. This is often the highest
  value-per-line command in an extension: an argument, a return, done.
- **`menu-bar`** — returns a `MenuBarExtra`. See `menu-bar.md`. `interval` is
  valid only here and is manifest-only: a preference cannot change it, because
  Raycast renders its own refresh control in the command's settings. Use `"1m"`.

Per-command `preferences` and `arguments` are scoped to that command and appear
in its own settings pane. Extension-level ones live at the top level and apply to
everything.

## arguments[]

Arguments are the escape hatch from ever opening the UI:

```json
"arguments": [
  { "name": "query", "type": "text", "placeholder": "status=failed", "required": false },
  { "name": "target", "type": "dropdown", "data": [{ "title": "Local", "value": "local" }] }
]
```

They are typed into root search before the command runs, and arrive as
`props.arguments`. A power user filtering a list should never have to open it.
Types: `text`, `password`, `dropdown`.

## preferences[]

```json
{
  "name": "binaryPath",
  "type": "textfield",
  "required": false,
  "title": "Binary Path",
  "description": "Absolute path. Raycast runs under launchd with no shell rc, so a bare name is never on PATH.",
  "placeholder": "/opt/homebrew/bin/mytool"
}
```

**The allowed `type` values are exactly:**

```text
appPicker | checkbox | dropdown | password | textfield | file | directory
```

**There is no multi-line type.** `"type": "textarea"` fails `ray lint` with
`must be equal to one of the allowed values`. `Form.TextArea` is a *form*
component inside a command and has nothing to do with `preferences[]`.

This bites in a way that produces no error at all: a preference documented as
"one entry per line" parses fine, its unit assertions pass, and it accepts
exactly one entry forever — because a `textfield` renders as a single-line input
and the user cannot type a newline. If you need a list, pick a single-line
separator (`;` between records, `|` between fields), accept newlines too in the
parser for values set by other means, and **say in the docs why the separator is
what it is** — otherwise a reader assumes it is arbitrary taste and reaches for a
newline.

Conventions that pay off:

- **A checkbox needs both `title` and `label`.** `title` is the row heading,
  `label` is the text beside the box.
- **A dropdown uses `data: [{ title, value }]`**, and its values become a string
  union in the generated types. Switch on that union rather than on `string`.
- **Put the trap in the description.** "Higher values cost one extra call per
  selection change." "Keeps a large queue from rendering hundreds of menu items
  every minute." The description is the only documentation most users read.
- **Surface invalid input rather than dropping it.** A malformed line that
  silently vanishes is indistinguishable from the feature being broken.

## The generated types

`ray build` writes `raycast-env.d.ts` with global namespaces derived from the
manifest, and **fields are typed as required**:

```ts
declare namespace Preferences {
  type Tasks = ExtensionPreferences & { showDetail: boolean; detailLogLines: string };
}
declare namespace Arguments {
  type Tasks = { query: string };
}
```

Consume them:

```ts
const prefs = getPreferenceValues<Preferences.Tasks>();
export default function Command(props: LaunchProps<{ arguments: Arguments.Tasks }>) {}
```

**Never hand-write a `Preferences` interface.** The whole value of the generated
file is that a manifest change which your code does not follow becomes a *compile
error*. A hand-written copy silently drifts, and a code default that disagrees
with the manifest default is a bug nobody ever finds.

Commit `raycast-env.d.ts`, list it in `tsconfig.json`'s `include`, and regenerate
with `npm run build` after every manifest edit.

Note that numeric preferences arrive as **strings** — a `textfield` is text.
`Number(prefs.detailLogLines) || 20`.

## Cross-command launching

```ts
import { launchCommand, LaunchType } from "@raycast/api";

await launchCommand({
  name: "queue-menu",
  type: LaunchType.Background,          // refresh a sibling without showing it
  context: { group: "archive" },
}).catch(() => {});                      // the target may be disabled by the user
```

The receiving command reads `props.launchContext`. Three rules, all learned by
observation rather than from the docs:

- **Always `.catch(() => {})`.** The target command can be disabled, and an
  unhandled rejection in a background launch is silent but real.
- **Push a view at most once.** The receiving command re-renders on every poll;
  without a `useRef` guard you stack duplicate views on the navigation stack.

  ```ts
  const pushed = useRef(false);
  useEffect(() => {
    if (pushed.current || !ctx?.logTaskId) return;
    pushed.current = true;
    push(<LogView id={ctx.logTaskId} />);
  }, [ctx?.logTaskId]);
  ```

- **Wait for dependent async state before acting on context.** If ids are
  per-account and the account selection is restored asynchronously, acting on the
  context immediately opens the *previous* account's data — which looks like it
  worked and shows someone else's record with the same id. Gate on the
  dependency:

  ```ts
  if (ctx.accountName && current.name !== ctx.accountName) return;
  ```

- **Context should win over remembered state.** If a command launches a sibling
  with an explicit filter, that filter must override the sibling's persisted
  choice, or "Show X in Group" silently shows a different group.

Also useful: `updateCommandMetadata({ subtitle })` rewrites *this* command's
subtitle in root search. Call it from a fetch's `onData`, and `.catch(() => {})`.

## Deeplinks

`open "raycast://extensions/<author>/<extension>/<command>"` opens a command from
outside Raycast — and shows a confirmation first:

> **Request to open ‹Command›** — The command was triggered from outside of
> Raycast.

Trust is per-command. Two consequences: an external nudge is a poor substitute
for `launchCommand` (which does not prompt), and some verification during
development genuinely needs a real keypress rather than a URL. A deeplink is
still the right tool for scripting a sequence of commands you are about to
screenshot.

## AI tools

```json
"tools": [{ "name": "list-tasks", "title": "List Tasks", "description": "…" }]
```

- **Raycast AI is Pro-gated**, so tools can never be the primary UX of an
  extension. Ship the commands first.
- Tools do not appear in root search. Raycast AI selects them by `description`,
  so the description is prompt engineering, not documentation.
- **There is no confirmation surface inside a tool call** comparable to
  `confirmAlert`. A first version must be read-only. Anything destructive needs a
  design for consent that does not exist yet in the tool API.
- If your transport already returns structured data, a tool is a thin wrapper —
  the work is prompt-shaped, not code-shaped.
