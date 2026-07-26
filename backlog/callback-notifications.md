# Callback-driven notifications

**Status:** `P?` · **Effort:** `[S]` · documented, not automated

## What

pueue runs a shell command whenever a task finishes — `daemon.callback` in
`pueue.yml`, rendered with Handlebars in **strict mode** (a typo'd variable is a
hard render error) and executed through the configured `shell_command`.

Variables, from `pueue/src/daemon/callbacks.rs` — note the wiki's list is
incomplete, omitting the last three:

| Variable | Value |
| --- | --- |
| `id`, `command`, `path`, `group` | the task |
| `result` | `TaskResult` Display string, or `"None"` if unfinished |
| `exit_code` | `"0"` on success, the code on failure, else `"None"` |
| `start`, `end` | **Unix timestamps** as strings, or `""` |
| `output` | the last `callback_log_lines` lines |
| `output_path` | absolute path to the task's log file |
| `queued_count`, `stashed_count` | still-queued / stashed tasks in the same group |

## Current decision

**Documented in the README, never written by the extension.** A user's
`pueue.yml` is theirs; silently editing it to make our menu bar feel faster is
not a trade we get to make on their behalf. The README carries a copy-pasteable
`osascript` recipe.

## The tempting version

A callback that fires a deeplink would make the menu bar update the instant a
task finishes, instead of within a minute:

```yaml
daemon:
  callback: 'open -g "raycast://extensions/da-wei_lee/pueue/queue-menu"'
```

## Why that isn't shipped

1. **It requires editing the user's config**, and the daemon must be restarted
   for changes to take effect — which kills every running task unless they are
   allowed to finish first.
2. `callback` holds **one** command. Setting ours would silently replace
   whatever notification the user already had. Composing safely means parsing
   and rewriting their existing value, in YAML, which is exactly the class of
   edit that goes wrong quietly.
3. Raycast prompts for confirmation on deeplinks triggered from outside the app
   ("The command was triggered from outside of Raycast"), so a background nudge
   would pop a dialog every time a task finished — worse than waiting a minute.

## What would have to be true

A way to trigger a background refresh from outside Raycast without a
confirmation prompt, plus a safe compose-with-existing-callback story. Until
then: document it, offer it as a copy action, and leave the file alone.
