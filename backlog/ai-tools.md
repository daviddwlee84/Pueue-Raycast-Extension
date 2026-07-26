# AI tools

**Status:** shipped · **Effort:** `[M]` · seven tools in `src/tools/`

## What shipped

`tools[]` in the manifest turns the extension into an AI Extension. Tools do not
appear in root search; Raycast AI calls them, choosing by `description`.

| Tool | Maps to | Confirms |
| --- | --- | --- |
| `get-tasks` | `status()` + client-side filtering | — |
| `get-task-log` | `status()` + `readLogText()` | — |
| `get-groups` | `status()` + `summarizeGroups()` | — |
| `add-task` | `mutate({op:"add"})` | Regular |
| `kill-tasks` | `mutate({op:"kill"})` | Destructive |
| `restart-failed` | `mutate({op:"restart"})` | Destructive when in place |
| `clean-tasks` | `mutate({op:"clean"})` | Destructive |

The transport already returned structured data, so each tool is a thin wrapper.
The work was prompt-shaped rather than code-shaped, as predicted — plus one piece
of real code, the projection in `src/lib/ai-shape.ts`.

## What the toolchain does, measured

On a scratch extension, before writing anything here:

| Claim | How it was checked |
| --- | --- |
| `ray lint` accepts a `tools[]` entry of `{name, title, description}` | added one; the only error was an unrelated short `description` on a command |
| `ray build` treats `src/tools/<name>.ts` as an entry point | `entry points [… "src/tools/running-tasks.ts"]` |
| `ray build -e dist` typechecks tool files | `checked TypeScript`, exit 0 |
| the manifest is **not** rewritten with a generated schema | `tools[]` was byte-identical after a build; the input schema is derived from the TypeScript `Input` type at bundle time |
| `Tool.Confirmation<T>` exists in the installed `@raycast/api` | `types/index.d.ts`, `namespace Tool` |

So the whole thing is covered by the existing gate: `just check` lints and
builds it, `just dist` typechecks it, and `just verify` asserts the projection.

## The objection that retired itself

An earlier version of this note said:

> there is no confirmation surface in an AI tool call comparable to
> `confirmAlert`. If this ships, the first version should be **read-only**.

That is no longer true. `Tool.Confirmation<Input>` runs *before* the tool with
the same input, returns `undefined` to skip or an object to prompt, and supports
`Action.Style.Destructive` plus an `info` list of name/value pairs. It is
strictly better than `confirmAlert` for this purpose, because the confirmation
can read the queue first and name what it is about to touch — `#7 #8 #9` rather
than "the wf group".

Every mutating tool here uses it. None of them is silent.

## Rules the tools follow

- **`envs` never reaches a model.** The transport strips it at the parse
  boundary; `toAiTask` then names every field explicitly rather than spreading,
  so a field pueue adds later cannot ride along. `dev-check.ts` asserts both the
  absence of `envs` and the exact key set.
- **An unknown connection name is an error.** The UI's `connectionByName` falls
  back to Local, which is right for a stale dropdown value and wrong for a
  sentence: task ids are per-daemon, so "kill everything on lab" landing here
  stops someone else's work. `resolveConnectionStrict` throws and lists the real
  names so the model can correct itself in one turn.
- **Results are capped and say so.** 100 tasks maximum, newest first, with
  `matched` / `returned` / `truncated` alongside — a silently short list is
  indistinguishable from a short queue.
- **`reset` is not exposed.** It kills and deletes everything in a group,
  running tasks included. No sentence is ambiguous enough to be worth that; it
  stays a deliberate action in the Groups view.

## Requirements

Raycast's docs state that AI Extensions need a Pro subscription. In practice a
configured custom model provider also works — this was developed against one.
Either way it is additive: tools are invisible in root search, and an install
with no AI access loses nothing.

## Still open

- **Evals.** Raycast supports `ai.yaml` eval files for tool selection. Not
  written. The failure they would catch is the model choosing `clean-tasks` when
  asked to "clear out" a group that has running work — the confirmation stops the
  damage, but a wrong tool choice is still a bad turn.
