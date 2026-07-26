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
  Toast,
  confirmAlert,
  getPreferenceValues,
  showToast,
} from "@raycast/api";
import { showFailureToast, type MutatePromise } from "@raycast/utils";

import { applyMutation } from "./optimistic";
import {
  firstLine,
  mutate as runMutation,
  PueueError,
  type Mutation,
  type State,
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
}

/**
 * Run a mutation with an optimistic paint and a delayed reconcile.
 *
 * Returns true when the mutation ran (i.e. wasn't cancelled at the confirmation).
 */
export async function act(
  mutation: Mutation,
  state: { mutate: MutatePromise<State | undefined>; revalidate: () => void },
  options: ActOptions,
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
    await state.mutate(runMutation(mutation), {
      optimisticUpdate: (data) => applyMutation(data, mutation),
      rollbackOnError: true,
      // The load-bearing line. See the module comment.
      shouldRevalidateAfter: false,
    });

    toast.style = Toast.Style.Success;
    toast.title = options.done;

    setTimeout(() => state.revalidate(), RECONCILE_DELAY_MS);
    setTimeout(() => state.revalidate(), RECONCILE_SETTLE_MS);
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
