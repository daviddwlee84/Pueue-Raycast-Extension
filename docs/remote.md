# Driving a remote pueued

pueue can't schedule *across* hosts, but a local client can drive a `pueued` on
another machine with full control — `status`, `log`, `follow`, `kill`, `add`.

This extension needs no protocol code for that: a connection is just a client
config, and pueue's own client does the rest. What it does need is to stop
assuming "local" in the four places that quietly did.

> The setup below is distilled from a run verified against a macOS client
> (pueue 4.0.4) driving a Linux `pueued` (4.0.2) over SSH socket forwarding.

## The short version

Preferences → **Remote Connections**, one entry:

```text
local_ubuntu
```

An SSH host you can already reach. Nothing else — no tunnel, no secret, no
config file. Every command runs as `ssh local_ubuntu 'pueue …'`.

More than one goes in that same field, separated by ` ; ` — Raycast has no
multi-line preference type, so the field is a single-line `textfield` and a
newline isn't something you can type into it:

```text
lab | local_ubuntu | ~/.cargo/bin/pueue ; gpu | gpu.example.com
```

**Why this is the default rather than a fallback.** Measured against a LAN host:

```text
plain ssh, one connection per call    200–400 ms
with ControlMaster multiplexing        10–30 ms
local pueue status --json              22–44 ms
```

A multiplexed remote read costs about the same as a local one, so there is
little left for the forwarded socket to buy. The extension always passes
`ControlMaster=auto -o ControlPersist=120`; ssh expires the shared connection
by itself, so unlike a tunnel there is nothing to start, watch, or clean up.

Requirements:

- `pueue` reachable on the remote host. **`ssh host 'cmd'` runs a
  non-interactive shell**, which reads no rc file — so a `cargo install` in
  `~/.cargo/bin` is invisible to it even though `which pueue` works when you log
  in. This is common enough that the connection can name the path:

  ```text
  lab | local_ubuntu | ~/.cargo/bin/pueue
  ```

  A third field after a *host* is the remote binary; after a *config path* it is
  an SSH host. They can't be confused, because a host never contains a slash.
  The `~` is expanded on the far side, not here.

  Without it you get `zsh:1: command not found: pueue`, which the extension
  detects and explains rather than passing through raw.
- Key or agent auth. `BatchMode=yes` is set, so a password prompt fails fast
  rather than hanging with no terminal to type into.

Submission works properly in this mode for free: the client runs on the remote
box, so the working directory is resolved where it actually exists. That is the
problem the rest of this document exists to work around.

## Advanced: reading through a forwarded socket

### 1. Forward the daemon's socket

The server needs **no reconfiguration** — it keeps its default unix socket.

```sh
ssh myhost 'ls /run/user/$(id -u)/pueue_*.socket'
# /run/user/1000/pueue_myuser.socket

ssh -f -N -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
    -L ~/.config/pueue/remote/remote.sock:/run/user/1000/pueue_myuser.socket \
    myhost
```

Remove the stale local socket file before re-forwarding. `autossh` or a launchd
agent keeps it up.

### 2. Copy the shared secret

The client authenticates with the *same* secret file as the daemon. It is a
credential: anyone holding it with a route to the socket can run commands as the
daemon's user.

```sh
mkdir -p ~/.config/pueue/remote && chmod 700 ~/.config/pueue/remote
ssh myhost 'cat ~/.local/share/pueue/shared_secret' > ~/.config/pueue/remote/shared_secret
chmod 600 ~/.config/pueue/remote/shared_secret
```

### 3. Write a client-only config

```yaml
# ~/.config/pueue/remote/client.yml
client:
  read_local_logs: false          # REQUIRED — see below
shared:
  use_unix_socket: true
  unix_socket_path: /Users/you/.config/pueue/remote/remote.sock
  shared_secret_path: /Users/you/.config/pueue/remote/shared_secret
```

Because the tunnel terminates at the daemon's own unix socket, **no TLS
certificate is involved**. Direct TCP+TLS is possible but requires editing the
server's config and restarting `pueued`, which interrupts a running queue.

### 4. Tell the extension

```text
gpu-box | ~/.config/pueue/remote/client.yml | myhost
```

A second field containing a `/` (or ending `.yml`) is read as a config path,
which selects socket mode. The third field is still an SSH host and is still
needed to submit — the socket cannot fix the working-directory problem. If the
field already holds a connection, put a `;` between them.

An entry the extension cannot parse is shown in the task list as **Unreadable
connection** rather than silently ignored.

A **Connection** submenu then appears in the Action Panel of Tasks and Groups
(⌘⇧N), and as a **Daemon** dropdown on the Add Task form. With no remote
connections configured, none of it renders.

## `read_local_logs: false` is mandatory

Set it. Two things depend on it:

1. **pueue itself** otherwise tries to read the remote daemon's log directory on
   your local filesystem.
2. **This extension** reads the same setting to decide whether its own on-disk
   log fast path is safe, so it can never disagree with pueue about where a log
   lives.

Left at the default `true`, the local log directory usually *exists* — so a read
returns a **different task's output under the same id** rather than failing.
Silently wrong, which is why it's a hard requirement rather than a suggestion.

## Submitting: use the SSH host

**This is the trap.** pueue records a task's working directory at submit time and
canonicalises it on whichever machine the **client** runs on. Submitting from
here against a remote daemon fails three ways:

| You do | What happens |
|---|---|
| submit from a local directory | The local path is sent. It doesn't exist on the daemon → `FailedToSpawn`, never runs. |
| type a remote-only path | The local client **refuses**: `Failed to canonicalize given working directory path`. |
| type `/tmp` from macOS | Silently rewritten to `/private/tmp` → fails on Linux. |

Only a path that exists *and canonicalises identically on both machines* works.
That rules out your actual project directories.

So when a connection has an SSH host, **Add Task submits with
`ssh <host> 'pueue add …'`** — the client runs on the far side, where the paths
are real. Reads and control commands keep using the forwarded socket, which is
faster and needs no second authentication.

The quoting that makes this safe is asserted, and was verified by pushing
`echo "double" && echo 'single' && echo HOME=$HOME` through the quoter and then
through a real shell.

## Other things worth knowing

- **Task ids are global to the daemon.** A remote connection sees and can remove
  *everyone's* tasks on that box. Be careful with Clean and Reset; prefer labels.
- **SSH mode has no version-skew problem.** The client runs on the remote box,
  so it is always the same version as its daemon. This only applies to socket
  mode.
- **A version mismatch warns but works.** A 4.0.4 client against a 4.0.2 daemon
  prints `Different protocol version detected` on every command and behaves
  normally. The extension filters that line out of stderr, so it can't become a
  toast title and hide the real error underneath.
- **`FailedToSpawn` is `Done`, not `Failed`,** with a dict-shaped result
  (`{"FailedToSpawn": "<os error>"}`). The extension treats any terminal result
  that isn't `Success` as a failure, so a job that never started is never
  reported as success.
- **Just SSH over instead**, for one-off submissions. `ssh myhost 'pueue add …'`
  needs no setup at all. A forwarded connection earns its keep when you want to
  *watch* a remote queue from here.

## If it doesn't work

| Symptom | Cause |
|---|---|
| `status` hangs | The tunnel is down. Re-forward. |
| secret mismatch | Re-copy `shared_secret`. |
| tasks land as `FailedToSpawn` | The working directory. Add an SSH host to the connection. |
| logs show the wrong output | `read_local_logs` is not `false`. |
| `command not found: pueue` | ssh uses a non-interactive shell. Add the remote path as a third field. |
