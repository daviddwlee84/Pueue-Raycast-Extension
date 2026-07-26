# Groups and batch progress

A group is pueue's unit of concurrency: every group has its own parallelism
limit and its own running/paused state, and a task belongs to exactly one. That
makes it the natural place to put a **batch** — queue twenty jobs into `wf`,
then watch them land.

This page is about the numbers the extension shows for that, what they mean, and
where each one deliberately stops short of guessing.

## What a group row shows

```text
◕  wf          6/20 done · 2 running · 3 failed        ~4m left    Running
```

| Part | Meaning |
| --- | --- |
| the ring | `done / total` as a filled circle |
| its colour | red if anything failed, orange if the group is paused, blue while running, green when finished and clean |
| `6/20 done` | finished tasks over every task in the group |
| `2 running` etc. | only the states that have tasks; zero terms are omitted |
| `~4m left` | an estimate — see [below](#the-eta) |
| the tag | the group's own state, which is not the same as its tasks' |

Press <kbd>⌘</kbd><kbd>⇧</kbd><kbd>D</kbd> for the detail pane, which adds the
average duration, the elapsed wall clock, and the ids that failed. The section
heading carries the same roll-up for the whole queue.

The menu bar shows a shorter form of the same thing — `wf · 6/20 · 3 failed`,
with a bar and the ETA inside the submenu.

## Why `total` counts everything in the group

pueue has no concept of a batch. There is no submission id, no run number,
nothing that marks "these twenty belong together". The only boundary that
actually exists is `pueue clean`.

So `total` is every task currently in the group, and a batch reads correctly if
you clean between runs:

```sh
pueue clean --group wf     # or the "Clean Finished Tasks in Group" action
pueue add -g wf -- ./job-1
pueue add -g wf -- ./job-2
# …
```

The alternative — inferring a batch from, say, "everything since the group last
went idle" — would be a guess presented as a fact, and it would be wrong for
exactly the long-running group where the number matters most.

## Finished means finished, not succeeded

`6/20 done` counts every task pueue reports as `Done`, whatever the outcome. A
task that exited 127 is finished; it is not going to run again on its own. The
failures are counted separately in the same line, and they are what turns the
ring red.

This matches `pqsum`, and it is the only reading under which the ring reaches
100 % when the queue has actually stopped working.

## The ETA

`~4m left` is `pending × average duration ÷ parallelism`. Three deliberate
choices sit behind it:

- **Pending means running, queued, and paused.** Stashed tasks are excluded. A
  stashed task is waiting on a person, not on the queue, so counting it would
  inflate the estimate without bound. It stays in `total`, because it is still
  work you asked for.
- **It needs at least two finished tasks.** With one sample the "average" is
  that one task, which is a guess wearing a number's clothes. Below two, the ETA
  renders as `—`.
- **Unlimited parallelism counts as one slot.** `parallel_tasks: 0` is pueue's
  unlimited; dividing by it would be a divide by zero, and one is the
  conservative answer.

It is an average, not a model. A group of twenty identical jobs gives a good
estimate; a group holding one 3-second linter and one 40-minute build gives a
bad one, and the `~` is there to say so.

## Elapsed

First start to last end — or to now, while something is still running or paused.
With nothing running it freezes, because a group with tasks queued behind a
paused slot has not been "running for three hours".

## Batch actions

Four actions operate on the whole group rather than on one task.

| Action | pueue equivalent | Notes |
| --- | --- | --- |
| Restart *n* Failed (New Tasks) | `restart --not-in-place --failed-in-group NAME` | Mints new ids. The originals keep their logs. |
| Restart *n* Failed in Place | `restart --in-place --failed-in-group NAME` | Reuses the ids and **overwrites the logs**. Read them first if you still need them. |
| Clean Finished Tasks in Group | `clean --group NAME` | Removes finished tasks and their logs. Nothing running or queued is touched. |
| Clean Only the Successes | `clean --group NAME --successful-only` | Leaves the failures in place so you can still read them. |

The restart actions are **hidden when the group has no failures**, not disabled.
pueue accepts `--failed-in-group` on a group with no failures — and on a group
that does not exist — and exits 0 without a word. An always-visible action that
silently did nothing would be worse than one that isn't there.

## Two operations that do more than their name says

Both were read out of `pueue --help`, and both confirmations spell it out:

- **Kill Running Tasks in Group** also **pauses** the group. Nothing further
  starts until you resume it.
- **Remove Group** **moves** its tasks to `default` rather than deleting them.

## Reset

Reset kills every task in the group, deletes them, and deletes their logs. It is
the only action in the extension that asks every single time, with no "don't
show this again".

In the menu bar it sits behind the <kbd>⌥</kbd> key **regardless of the
Confirmations preference** — the one exception, because a menu bar has no dialog
available (a Raycast alert cannot render while the menu is open) and this is the
one action where a mis-click cannot be undone. If you would rather have the real
alert, `Open Groups…` is directly below it.

## Parallelism

The submenu offers 1–32 and Unlimited, plus a `Custom…` field for anything else
— pueue takes any non-negative integer, and a machine with 64 cores has every
right to ask for 48.

The presets are a static list on purpose. Seeding them from this machine's core
count would be wrong the moment you point the extension at a remote daemon, and
a plausible wrong number is worse than a list that makes no claim.
