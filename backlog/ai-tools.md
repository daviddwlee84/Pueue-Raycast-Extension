# AI tools

**Status:** `P?` · **Effort:** `[M]` · not started

## What

A `tools[]` array in the manifest turns the extension into an AI Extension.
Tools don't appear in root search; Raycast AI calls them, using each tool's
`description` to decide when.

Natural candidates, all of which map onto existing transport calls:

| Tool | Maps to |
| --- | --- |
| what's running | `status()` filtered to running |
| queue this | `mutate({op: "add", …})` |
| why did task N fail | `status()` + `readLogText(N)` |
| kill everything in group X | `mutate({op: "kill", group: X})` |

The transport already returns structured data, so a tool is a thin wrapper — the
work is prompt-shaped, not code-shaped.

## Why it isn't done

**AI Extensions require Raycast Pro.** From
[developers.raycast.com/ai/getting-started](https://developers.raycast.com/ai/getting-started):

> "To use AI APIs or AI Extensions, you need to subscribe to Raycast Pro."

So this can only ever be an additive layer for Pro subscribers, never the
primary way to use the extension. Everything it would offer already exists as a
command.

## Caution if it is built

Mutating tools are the risk. An AI that decides "kill everything in the default
group" from an ambiguous sentence is worse than no tool, and there is no
confirmation surface in an AI tool call comparable to `confirmAlert`. If this
ships, the first version should be **read-only** — status, logs, why-did-this-
fail — with `add` at most, and nothing destructive.

## What would have to be true

A Pro subscription to test against, and a decision that read-only tools are
worth the manifest surface.
