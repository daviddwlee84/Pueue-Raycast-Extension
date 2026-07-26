# A Rust I/O error rendered as if the task had printed it

## Symptoms (grep this section)

```text
(Pueue error) Failed to get log file handle: I/O error at path
"/Users/you/Library/Application Support/pueue/task_logs/1.log"
while getting log file handle:
No such file or directory (os error 2)
```

…shown in the task's **Output** pane, as though the command had written it.

## Cause

`pueue log <id> --json` does not fail when the log file is missing. It exits
**0** and puts its own error message in the `output` field, where the task's
output belongs:

```console
$ pueue log 1 --json          # task 1 is stashed and has never run
{"1":{"task":{…},"output":"(Pueue error) Failed to get log file handle: …"}}
$ echo $?
0
```

There is no exit code, no separate field, and no `error` key to branch on.

## Fix

Two levels, because either alone is insufficient:

1. **Don't ask.** `hasEverRun(task)` is false when the status carries no `start`,
   so stashed and queued tasks never trigger the call at all. This also saves a
   subprocess spawn per selection change.
2. **Detect it anyway.** `cleanLogOutput()` treats an output beginning with
   `(Pueue error)` as absent. Needed because a log can also be cleaned away
   underneath a task that *did* run, which level 1 doesn't cover.

The UI then distinguishes the two honestly: *"This task hasn't run yet."* versus
*"No output."*

## Note

The detection is a prefix check, not a substring one — a build log that happens
to contain the words "(Pueue error)" mid-stream is still real output, and there
is an assertion for exactly that case.

## Related

`pueue log <unknown-id> --json` is `{}` with exit 0 too. Empty is not an error.
