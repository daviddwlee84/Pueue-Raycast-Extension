/**
 * Turning a `Mutation` into a remote command line.
 *
 * `ssh host '<command>'` hands its argument to the **remote** shell, so the
 * whole pueue invocation has to survive one round of shell parsing on the far
 * side. That is why this exists as its own quoting layer, separate from
 * `argvFor` — which is deliberately quote-free because `execFile` needs no
 * quoting at all.
 *
 * Submitting over SSH is not an optimisation. pueue canonicalises a task's
 * working directory on the **client**, so a job submitted from here against a
 * remote daemon either lands in a directory that doesn't exist there
 * (`FailedToSpawn`, never runs) or is refused outright with "Failed to
 * canonicalize given working directory path". Running the client on the remote
 * box makes the paths real.
 */

/**
 * POSIX single-quote a string.
 *
 * Single quotes suppress every expansion, so the only character needing care is
 * the single quote itself — closed, escaped, reopened. This is the standard
 * `'\''` trick and it is safe for newlines, `$`, backticks, and everything else.
 */
export function shellQuote(s: string): string {
  return `'${s.replaceAll("'", `'\\''`)}'`;
}

/** Quote an argv into one command line for a remote shell. */
export function shellJoin(argv: readonly string[]): string {
  return argv.map(shellQuote).join(" ");
}

/**
 * The full `ssh` argv for running a pueue command on a remote host.
 *
 * `-o BatchMode=yes` fails fast instead of blocking on a password prompt —
 * there is no terminal to type into, so a prompt would just hang until the
 * timeout. Key-based auth or an agent is assumed, which is the same assumption
 * the SSH-forwarded socket setup already makes.
 */
export function sshArgv(host: string, pueueArgv: readonly string[]): string[] {
  return [
    "-o",
    "BatchMode=yes",
    host,
    // `pueue` unqualified: the remote box's own PATH resolves it, and we have
    // no way to know where it lives there.
    shellJoin(["pueue", ...pueueArgv]),
  ];
}
