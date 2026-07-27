# Pitfalls

One file per trap, titled by the **symptom** you'd search for — not the root
cause, which you don't know yet when you're looking.

Each file opens with a "Symptoms (grep this section)" block containing the
verbatim error text, so `grep -r` over this directory finds it.

| File | Symptom |
| --- | --- |
| [raycast-launchd-path-pueue-not-found.md](raycast-launchd-path-pueue-not-found.md) | Works in the terminal, `spawn pueue ENOENT` from Raycast |
| [pueue-color-flag-must-precede-subcommand.md](pueue-color-flag-must-precede-subcommand.md) | `error: unexpected argument '--color' found`, exit 2 |
| [pueue-status-enum-is-externally-tagged.md](pueue-status-enum-is-externally-tagged.md) | `task.status` is an object, `status.Done.result` is sometimes a string |
| [pueue-log-json-hides-errors-in-output.md](pueue-log-json-hides-errors-in-output.md) | A Rust I/O error rendered as if the task printed it |
| [daemon-state-lags-one-poll-after-a-mutation.md](daemon-state-lags-one-poll-after-a-mutation.md) | A killed task flips back to Running, then to Done a moment later |
| [ray-build-does-not-typecheck.md](ray-build-does-not-typecheck.md) | `ray build` succeeds on code `tsc` rejects |
| [cached-list-renders-a-dead-queue-as-live.md](cached-list-renders-a-dead-queue-as-live.md) | Tasks shown as Running after the daemon stopped |
| [remote-task-working-directory-fails-to-spawn.md](remote-task-working-directory-fails-to-spawn.md) | A remote task ends `FailedToSpawn` immediately, or won't submit |
| [raycast-preferences-cannot-be-multiline.md](raycast-preferences-cannot-be-multiline.md) | A "one per line" preference only ever holds one entry |
| [usecachedpromise-serves-other-cache-keys.md](usecachedpromise-serves-other-cache-keys.md) | Switching connection shows the previous one's data, with no error |
| [ray-lint-checks-more-in-ci.md](ray-lint-checks-more-in-ci.md) | `ray lint` green locally, red in CI on `validate package-lock.json` |
