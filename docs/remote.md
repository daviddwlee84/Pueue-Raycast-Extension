# Driving a remote pueued

pueue can't schedule *across* hosts, but a local client can drive a `pueued` on
another machine with full control — `status`, `log`, `follow`, `kill`, `add`.

This extension needs no protocol code for that: a connection is just a client
config, and pueue's own client does the rest. What it does need is to stop
assuming "local" in the four places that quietly did.

> The setup below is distilled from a run verified against a macOS client
> (pueue 4.0.4) driving a Linux `pueued` (4.0.2) over SSH socket forwarding.

## Setting it up

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

Extension preferences → **Remote Connections**, one per line:

```text
gpu-box | ~/.config/pueue/remote/client.yml | myhost
```

The third field is an SSH destination and is optional — but see *Submitting*.
Anything `ssh` accepts works, including a `~/.ssh/config` alias.

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
