/**
 * Running pueue on another machine through `ssh`.
 *
 * Two separate concerns live here.
 *
 * **Quoting.** `ssh host '<command>'` hands its argument to the *remote* shell,
 * so the whole pueue invocation has to survive one round of shell parsing on
 * the far side. That is why this exists apart from `argvFor`, which is
 * deliberately quote-free because `execFile` needs no quoting at all.
 *
 * **Multiplexing.** This is what makes SSH mode viable rather than merely
 * possible. Measured against a LAN host:
 *
 *     plain ssh, one connection per call    200–400 ms
 *     with ControlMaster                     10–30 ms
 *
 * A local `pueue status --json` is 22–44 ms, so a multiplexed remote read costs
 * about the same as a local one — while a naive one would make the task list
 * feel broken. Every ssh invocation therefore shares one connection.
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

/**
 * Quote a path that must be interpreted on the *remote* host.
 *
 * `~` and `$HOME` have to survive to the far side, and single quotes would kill
 * both. Double quotes keep the expansion while still protecting spaces —
 * verified against a real host, where `"$HOME/.cargo/bin/pueue" --version`
 * answered correctly. Anything without a leading ~ or $ is quoted normally.
 */
export function quoteRemotePath(p: string): string {
  if (p.startsWith("~/")) return `"$HOME/${p.slice(2)}"`;
  if (p.startsWith("$")) return `"${p}"`;
  return shellQuote(p);
}

/** Quote an argv into one command line for a remote shell. */
export function shellJoin(argv: readonly string[]): string {
  return argv.map(shellQuote).join(" ");
}

/**
 * Connection-sharing options.
 *
 * `%C` is a hash of (local host, remote host, port, user) — short, unique, and
 * stable, which matters because a control socket path has the ~104 byte unix
 * socket limit. `/tmp` rather than the extension's support directory for the
 * same reason: Raycast's support paths are long.
 *
 * `ControlPersist=120` lets the shared connection linger two minutes after the
 * last command, so a burst of reads pays the handshake once. Nothing needs
 * cleaning up — ssh expires it by itself, which is the whole reason this is
 * preferable to managing a forwarded tunnel.
 */
export function multiplexArgs(): string[] {
  return [
    "-o",
    "ControlMaster=auto",
    "-o",
    "ControlPath=/tmp/pueue-rc-%C",
    "-o",
    "ControlPersist=120",
  ];
}

/**
 * How long to wait for the TCP connection, in seconds.
 *
 * Without this ssh does not give up at all: a host that black-holes packets
 * (no RST, no ICMP) left ssh still trying after 45 seconds, and only our own
 * exec timeout ended it — 30 seconds during which the menu bar command is
 * blocked and its own menu items don't respond. Measured; with
 * ConnectTimeout=5 the same host fails in 5.1 s.
 *
 * Five seconds is generous for a LAN box or a reachable server, and short
 * enough that a wrong hostname feels like an error rather than a hang.
 */
const CONNECT_TIMEOUT_S = 5;

/**
 * Bound a connection that opens and then stalls mid-command.
 *
 * ConnectTimeout only covers the TCP handshake. A link that drops after the
 * session is established would otherwise hang until the exec timeout, so ask
 * for keepalives and give up after three missed ones.
 */
const ALIVE_INTERVAL_S = 5;
const ALIVE_COUNT_MAX = 3;

/** Options that stop an unreachable host from hanging the UI. */
export function reachabilityArgs(): string[] {
  return [
    "-o",
    `ConnectTimeout=${CONNECT_TIMEOUT_S}`,
    "-o",
    `ServerAliveInterval=${ALIVE_INTERVAL_S}`,
    "-o",
    `ServerAliveCountMax=${ALIVE_COUNT_MAX}`,
  ];
}

/**
 * True when ssh could not reach the host at all, as opposed to pueue failing.
 *
 * Worth separating: the remedy is "check the host, the tunnel, or your
 * network", which has nothing to do with pueue or the daemon. Left
 * unclassified these arrive as a bare ssh diagnostic under a "Pueue command
 * failed" heading, which points the reader at the wrong thing entirely.
 */
export function isSshUnreachable(stderr: string): boolean {
  return /(ssh: connect to host|Operation timed out|Connection timed out|No route to host|Could not resolve hostname|Network is unreachable|Connection refused|Host key verification failed|Permission denied \(publickey)/i.test(
    stderr,
  );
}

/**
 * The full `ssh` argv for running a pueue command on a remote host.
 *
 * `BatchMode=yes` fails fast instead of blocking on a password prompt — there
 * is no terminal to type into, so a prompt would hang until the timeout. Key
 * or agent auth is assumed.
 */
export function sshArgv(
  host: string,
  pueueArgv: readonly string[],
  remoteBinary?: string,
): string[] {
  // Unqualified by default: the remote box's own PATH resolves it, and we
  // cannot know where it lives there. But `ssh host 'cmd'` runs a
  // *non-interactive* shell, which reads no rc file — so a cargo install in
  // ~/.cargo/bin is invisible, and the connection can name the path instead.
  const binary = remoteBinary
    ? quoteRemotePath(remoteBinary)
    : shellQuote("pueue");

  return [
    "-o",
    "BatchMode=yes",
    ...reachabilityArgs(),
    ...multiplexArgs(),
    host,
    [binary, shellJoin(pueueArgv)].filter(Boolean).join(" "),
  ];
}

/**
 * True when a remote command failed because pueue isn't on the far side's PATH.
 *
 * Worth detecting specifically: the message is a bare shell error with no
 * mention of pueue's absence being the *point*, and the fix (an absolute path,
 * because ssh uses a non-interactive shell) is not obvious.
 */
export function isRemoteBinaryMissing(stderr: string): boolean {
  return /(command not found|No such file or directory).*pueue|pueue.*(command not found|not found)/i.test(
    stderr,
  );
}
