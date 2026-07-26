/**
 * Running a mutation and keeping the list honest afterwards.
 *
 * The whole file exists to work around one thing: pueue's daemon acknowledges a
 * request before its update loop applies it, so an immediate revalidate reads
 * pre-change state. `useCachedPromise`'s `mutate` does exactly that by default.
 * Left alone, every action would visibly undo itself and then redo itself.
 */

import {
  Alert,
  LaunchType,
  Toast,
  confirmAlert,
  getPreferenceValues,
  launchCommand,
  showToast,
} from "@raycast/api";
import { showFailureToast, type MutatePromise } from "@raycast/utils";

import { applyMutation } from "./optimistic";
import {
  firstLine,
  mutate as runMutation,
  PueueError,
  type Connection,
  type Mutation,
  type Snapshot,
} from "./pueue";

/**
 * How long to wait before reading the daemon back.
 *
 * Measured, not guessed. Killing a running task and polling `status --json`
 * until it reports Done, five trials on this machine:
 *
 *     min 278 ms   median 284 ms   max 297 ms
 *
 * — the daemon acks in ~22 ms and its update loop applies the change ~280 ms
 * later. That is why revalidating immediately is wrong, and 400 ms clears the
 * observed worst case with room to spare.
 */
const RECONCILE_DELAY_MS = 400;

/**
 * A second, later read.
 *
 * The delay above is tuned for an idle local daemon; a loaded one can take
 * longer. Rather than pick a pessimistic single delay that makes every action
 * feel sluggish, read twice — once quickly, once late enough to be certain.
 * This is also what keeps a mis-tuned first delay from being visible at all.
 */
const RECONCILE_SETTLE_MS = 1_500;

/**
 * Ask the menu bar to re-read, rather than leaving it up to a minute stale.
 *
 * This is the documented use of `launchCommand` with `LaunchType.Background` —
 * forcing a sibling command's background refresh. It resolves when the command
 * is *launched*, not when it finishes, so awaiting it would buy nothing.
 *
 * It throws when the target command is disabled, which is a perfectly normal
 * state (menu bar commands are off by default for store installs) and not
 * something to report — hence the swallowed rejection.
 */
function nudgeMenuBar(): void {
  // Give the daemon's update loop the same head start the reconcile gets, so
  // the menu bar doesn't re-read the pre-change state we just worked around.
  setTimeout(() => {
    launchCommand({ name: "queue-menu", type: LaunchType.Background }).catch(
      () => {},
    );
  }, RECONCILE_DELAY_MS);
}

export interface ActOptions {
  /** Present continuous, e.g. "Killing task 4". Becomes the toast title. */
  verb: string;
  /** Past tense, e.g. "Killed task 4". */
  done: string;
  confirm?: {
    title: string;
    message?: string;
    /** Shown as the confirm button. Defaults to the verb. */
    actionTitle?: string;
    destructive?: boolean;
    /** Offers "Do not show this message again". Omit for genuinely rare, costly actions. */
    rememberChoice?: boolean;
  };
  /** Which daemon to act on. Omitted means the default connection. */
  connection?: Connection;
}

/**
 * Run a mutation with an optimistic paint and a delayed reconcile.
 *
 * Generic over what the calling hook holds, because the two list views cache
 * different shapes: Tasks caches a whole `State`, Groups caches a `GroupMap`.
 * Each supplies the pure updater for its own shape.
 *
 * Returns true when the mutation ran (i.e. wasn't cancelled at the confirmation).
 */
export async function act<T>(
  mutation: Mutation,
  state: { mutate: MutatePromise<T | undefined>; revalidate: () => void },
  options: ActOptions,
  optimistic: (data: T | undefined, m: Mutation) => T | undefined,
): Promise<boolean> {
  const prefs = getPreferenceValues<Preferences>();

  if (options.confirm && prefs.confirmDestructive) {
    const ok = await confirmAlert({
      title: options.confirm.title,
      message: options.confirm.message,
      rememberUserChoice: options.confirm.rememberChoice,
      primaryAction: {
        title: options.confirm.actionTitle ?? options.verb,
        style: options.confirm.destructive
          ? Alert.ActionStyle.Destructive
          : Alert.ActionStyle.Default,
      },
    });
    if (!ok) return false;
  }

  const toast = await showToast({
    style: Toast.Style.Animated,
    title: options.verb,
  });

  try {
    await state.mutate(
      runMutation(mutation, { connection: options.connection }),
      {
        optimisticUpdate: (data) => optimistic(data, mutation),
        rollbackOnError: true,
        // The load-bearing line. See the module comment.
        shouldRevalidateAfter: false,
      },
    );

    toast.style = Toast.Style.Success;
    toast.title = options.done;

    setTimeout(() => state.revalidate(), RECONCILE_DELAY_MS);
    setTimeout(() => state.revalidate(), RECONCILE_SETTLE_MS);
    nudgeMenuBar();
    return true;
  } catch (error) {
    // pueue refuses some things outright — removing a running task, for one.
    // Its prose is better than anything we'd write, so show it.
    await showFailureToast(error, {
      title: options.verb,
      message:
        error instanceof PueueError ? firstLine(error.detail) : undefined,
    });
    return false;
  }
}

/**
 * `act` bound to the `Snapshot` both list views cache.
 *
 * Groups reads the same `status --json` payload Tasks does, so one updater
 * serves both — and a group-scoped kill correctly flips its tasks too, which
 * is what the per-group progress numbers are computed from.
 *
 * The optimistic paint reaches inside the snapshot and leaves the stamps
 * alone: a predicted state still belongs to this connection, and it is still as
 * old as the read it was predicted from.
 */
export function actOnTasks(
  mutation: Mutation,
  state: {
    mutate: MutatePromise<Snapshot | undefined>;
    revalidate: () => void;
  },
  options: ActOptions,
): Promise<boolean> {
  return act(mutation, state, options, (snap, m) =>
    snap ? { ...snap, state: applyMutation(snap.state, m) } : snap,
  );
}
