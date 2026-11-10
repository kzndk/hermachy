# Hermachy Security

Hermachy deliberately joins a remote Hermes host and an Omarchy workstation into one agent execution boundary. Read this document before installation.

## Fail-closed routing in the shared gateway

Hermachy 0.6 runs in the remote host's default-profile Hermes gateway: the same
process that serves Hermes Desktop, messaging, and the profile's other surfaces.
Hermachy 0.5.x enforced its boundary with a process-wide environment pin in a
private, patched Hermes runtime. That runtime no longer exists. Enforcement now
lives in two places:

1. **The bridge.** `hermachy-server`, the forced command, is the only way the
   Omarchy client key reaches the gateway. It forwards only allowed methods and
   rewrites new sessions; see [Bridge policy](#bridge-policy).
2. **The plugin.** The Hermachy plugin in the default profile wraps every tool
   call in a `tool_execution` middleware. For a bound session, the middleware
   binds a terminal scope for the plugin's `hermachy_ssh` provider and guards the
   environment cache. Unbound sessions pass through untouched.

For bound sessions, the plugin guarantees all of the following:

- terminal, process, file (`read_file`, `write_file`, `patch`, `search_files`),
  and `execute_code` tools use the same Omarchy SSH route, starting in the bound
  workstation directory;
- only Hermachy's dedicated execution identity is used, with strict host keys,
  `IdentitiesOnly=yes`, no SSH agent, no forwarding, public-key authentication
  only, and a private ControlMaster socket in its own mode-`0700` directory;
- no Hermes profile, credential, skill, or cache synchronization to Omarchy, and
  no sync-back;
- no environment passthrough from the host;
- dangerous-command approvals stay on;
- a host-only directory (Hermachy's workspace marker, the Hermes home, or the
  host user's home) is never sent as the workstation working directory;
- an environment cached before the session was bound, or for another route, is
  evicted and never reused, and an unbound session never reuses a workstation
  environment;
- malformed, missing, or unreadable bindings, configuration, or keys fail closed.

Hermes skips a middleware callback that raises and runs the tool anyway. The
Hermachy middleware therefore never raises: every failure becomes an error result
and nothing is executed. An SSH loss, timeout, or execution exception during a
command returns a result marked `hermachy_outcome_unknown` instead of raising, so
Hermes's retry loop never re-runs it automatically. Routing is explicit per call,
with no fallback to the other machine on failure. Do not remove or bypass these
checks.

Limits of this model:

- The execution private key is now readable by the shared gateway process, which
  also runs the profile's messaging and other surfaces. That process runs as the
  same operating-system user as before, so no OS isolation is lost. What is lost
  is 0.5.x's process-level property that the Hermes process could route only to
  Omarchy.
- The plugin acts only when the running gateway has loaded it. The bridge
  therefore asks the running gateway (`plugins.list`) on every attach, before it
  relays any client frame, and refuses the connection unless the gateway loaded
  the Hermachy plugin of the same version without a load error. `hermachy
  --doctor` performs the same check. Restart the gateway after installing or
  upgrading the plugin, and verify the execution location as described in the
  [setup guide](docs/OMARCHY_REMOTE_HERMES.md#11-verify-execution-location).
- A bypass session (`safe_mode` or `ignore_user_config`) does not load plugins.
  The bridge refuses bypass sessions for Hermachy connections only. If another
  surface of the profile opens a bound chat in a bypass session, that chat's
  tools run on the host.
- In the default profile, Hermachy shares the profile's permanent ("always")
  approval allowlist with all other surfaces. A command approved permanently in
  one surface is also approved in Hermachy chats, and the reverse.

## Trust model

### Omarchy-to-remote-host key

The dedicated `~/.ssh/hermachy_to_morpheus` private key stays on Omarchy. The filename is a legacy default; it works with any remote Hermes host. Only its public key is installed on the remote host with OpenSSH's `restrict` option and a forced command.

The key:

- may invoke only `hermachy-server`;
- may not allocate a PTY;
- may not obtain an ordinary remote-host shell;
- may not forward ports, agents, or X11;
- may not run user SSH rc files.

The forced server accepts only `hermachy-v2` plus a bounded, URL-safe base64 JSON request. It does not evaluate the request as shell text. Supported actions are `doctor`, `resolve`, and `attach`. Requests from 0.5.x clients (`hermachy-v1`) are refused with an upgrade message.

**Important:** the forced command limits SSH, not Hermes itself. Hermes gives every attached client full operator privilege, so the bridge is the policy point. Anyone holding the Omarchy client key can use the remote host's default Hermes profile: its chats, memory, skills, and agent, and the gateway methods the bridge forwards, which include profile configuration, skill, plugin, MCP server, and vault management. In bound chats the agent can also run commands on the host with `hermes_host`, subject to the host's approvals. Treat the client key as able to act on the remote host as `REMOTE_USER` through the agent. Protect the Omarchy account and private key accordingly.

### Remote-host-to-Omarchy key

The dedicated `~/.ssh/hermachy_to_omarchy` private key stays on the remote Hermes host, where the Hermachy plugin in the shared gateway process uses it. Only its public key is installed on Omarchy with OpenSSH's `restrict` option and a forced `hermachy-exec` entry point.

The key intentionally permits arbitrary non-PTY shell commands as the normal Omarchy user. This is the mechanism used by the plugin's `hermachy_ssh` environment for the terminal, process, file, patch, search, and code-execution tools of bound chats, and for reading workstation project instructions with `hermachy-exec --context-files`.

The workstation entry point imports an allowlist of desktop connection settings
from the local user service manager. It supplies the current display, user bus,
and command paths without importing provider credentials or shell startup code
from that manager. It is an environment bridge, not a command sandbox.

`hermachy-exec --admin` requests root execution through polkit in a transient
user service, so the workstation's graphical authentication agent handles the
password. It preserves arguments and exit status and stops the transient service
on caller cancellation. No sudoers or passwordless polkit rule is installed.
Authorization cancellation is a failed command and must not be automatically
retried. The executable requested for root execution must be trusted by the user.

The key:

- cannot forward ports, agents, or X11;
- cannot allocate a PTY;
- does not receive passwordless sudo;
- has the normal Omarchy user's permissions for non-interactive commands.

A compromised remote host, a compromised surface or plugin running in the shared gateway process, or a stolen execution key therefore exposes data and commands available to that Omarchy user.

## Bridge policy

For each TUI connection or reconnection, `hermachy-server` runs the host's own
`hermes --profile default gateway attach --json`, with a minimal environment
rather than sshd's or the client's. Hermes discovers the running gateway and mints
a 30-second single-use ticket. The bridge then dials the gateway's loopback
WebSocket. It never starts a gateway under sshd; if the gateway is not running, it
fails with Hermes's own message.

Every client frame is checked before it is forwarded:

- Only methods declared by the pinned Hermes revision are forwarded. The list is
  generated into `src/hermachy/attach_methods.py` by
  `scripts/update_attach_methods.py`. Methods that a newer Hermes adds are refused
  until a Hermachy release reviews them.
- These declared methods are refused:
  - host execution: `cli.exec` and `shell.exec`;
  - host file, clipboard, and path reads: `file.attach`, `image.attach`,
    `pdf.attach`, `clipboard.paste`, `input.detect_drop`, `image.render`,
    `complete.path`, the host project-folder methods (`projects.add_folder`,
    `projects.remove_folder`, `projects.discover_repos`, `projects.record_repos`,
    `projects.tree`, `projects.create`), and the foreign-session methods
    (`session.foreign.*`);
  - secrets leaving the host: `vault.preview.fill`;
  - workspace and profile changes: `session.cwd.set`, `session.workspace.move`,
    workspace `session.mutate`, any `profile` parameter other than `default`, and
    every `profile.*` or `profiles.*` method;
  - owner, worker, and relay verbs: `cron.submit`, `cron.recover`, `cron.cancel`,
    `kanban.run`, `a2a.forward`, `worker.*`, and `bot_relay.*`.
- `session.create` is rewritten. The session is pinned to the default profile and
  `client_env` is stripped, so client-side variables never reach host
  subprocesses. `safe_mode` and `ignore_user_config` are refused because bypass
  sessions skip the plugin. `cwd_explicit`, `row_cwd`, `toolsets`, and
  `follow_profile_config` are refused. The session's host working directory is set
  to the connection's workspace marker,
  `<hermes home>/hermachy/workspaces/<connection id>`.
- Batched and non-object frames are refused. The client's answers to gateway
  requests, such as approvals and clarifications, pass through.

A refused frame receives a JSON-RPC error and never reaches the gateway. Re-review
this policy for every Hermes upgrade.

Before the gateway's answer to `session.create` or `session.resume` reaches the
client, the bridge binds that session to the connection. The client cannot submit
a turn before it has the session ID, so a bound session never runs a tool unbound.
The records are mode-`0600` JSON files in mode-`0700` directories:

```text
<hermes home>/hermachy/connections/<connection id>.json   # workstation directory
<hermes home>/hermachy/sessions/<session id>.json         # session binding
```

The plugin reads a record only if it is a regular file, owned by the Hermes user,
and not accessible to group or other users; it does not follow a symlinked record.
A binding belongs to the chat and persists: a bound chat continued from Hermes
Desktop or another surface still runs its tools on Omarchy. Hermachy 0.6.0 has no
command to unbind a chat.

## Network boundary

Both SSH connections use Tailscale addresses and are intended to be reachable only inside the tailnet. Hermachy does not create a Tailscale Serve/Funnel route and does not expose a new network listener.

On Omarchy, the client's relay listens only on `127.0.0.1`, on an ephemeral port
and an unguessable path, for the lifetime of one `hermachy` process. It accepts
connections only from `127.0.0.1` and only on that path. The URL is passed to the
local TUI in its environment, so other processes of the same Omarchy user are
trusted, as before. On the remote host, the bridge connects only to the loopback
WebSocket that the gateway offers.

The Hermachy client ignores ambient user SSH configuration with:

```text
-F /dev/null
```

Its SSH policy includes:

- `BatchMode=yes`
- `IdentitiesOnly=yes`
- `StrictHostKeyChecking=yes`
- an explicit dedicated identity file
- disabled agent and port forwarding
- bounded connection timeouts and keepalives

The plugin applies equivalent dedicated-identity isolation to the gateway's outbound Omarchy connection. It also disables the SSH agent and password or keyboard-interactive authentication, and uses a private ControlMaster socket that no other session or process shares.

`StrictHostKeyChecking=yes` requires the exact destination key to be recorded
before the first Hermachy connection. Verify both destination fingerprints
out-of-band, populate each user's `known_hosts`, and treat either a missing or
mismatched key as a security event. Never work around it with
`StrictHostKeyChecking=no`.

## Shared gateway and explicit host execution

Hermachy no longer runs a private gateway or supervisor. The default profile's
gateway is its normal launchd or systemd service. Hermachy attaches to it for each
TUI connection and never starts or stops it. Closing the TUI ends its SSH channel
and bridge process; the chat remains in the profile.

The Hermachy plugin's `hermes_host` tool runs a foreground command on the Hermes
host, for up to 300 seconds, as the remote Hermes user, using the host's local
environment and command approval checks. It therefore has access to that user's
remote files and programs. This is an intentional part of the trust boundary. It
replaces 0.5.x's `target` parameter, exists only in bound chats (elsewhere the
normal terminal already runs on the host), and never copies profile state between
hosts.

## Secrets and authoritative state

With the plugin active, authoritative storage for these remains on the remote
Hermes host, without automatic synchronization to Omarchy:

- model and provider credentials;
- `.env`, `auth.json`, and credential pools;
- memory and user profile;
- skills and cache;
- session database and transcripts;
- Hermachy session bindings;
- the authoritative Hermes configuration.

The local renderer receives conversation and tool output for display. This
storage boundary does not prevent an authorized agent command from reading
remote files and returning their contents to the client.

The Omarchy client configuration contains only the remote hostname, username, port, and local path to its dedicated private key. The local renderer keeps its own UI state in `~/.local/state/hermachy`; it is not a copy of the remote profile. The client's relay appends SSH and remote bridge error output to `~/.local/state/hermachy/logs/relay.log` (mode `0600`) so it never draws over the TUI. The doctor response does not print identity paths or key material.

The remote server configuration, `~/.config/hermachy/server.json`, contains the Omarchy host, user, and port, the path of the execution private key, the Hermes launcher, and the Hermes home.

## File permissions

Hermachy writes:

- private keys as mode `0600`;
- JSON endpoint configuration as mode `0600`;
- session binding and connection records as mode `0600`;
- the client's relay log as mode `0600`;
- `authorized_keys` as mode `0600`;
- `.ssh`, private config, local state, and binding directories as mode `0700`;
- executable zipapps as mode `0755`;
- plugin files as mode `0644`;
- public keys as mode `0644`.

Server startup rejects a server configuration or execution private key owned by another user or accessible to group/other users. The plugin applies the same check to the server configuration, the execution private key, and every binding record.

## Installer behavior

Installer-managed `authorized_keys` sections have exact markers:

```text
# >>> hermachy:studio-executor >>>
# <<< hermachy:studio-executor <<<

# >>> hermachy:omarchy-client >>>
# <<< hermachy:omarchy-client <<<
```

The `studio` name is current Hermachy protocol terminology for the remote Hermes host.

Each managed `authorized_keys` block update is atomic and idempotent. Content outside the matching block is preserved. Malformed or duplicate markers cause a fail-closed error rather than a rewrite. Only Ed25519 public keys with a structurally valid OpenSSH key blob are accepted.

`studio-finish` also copies the plugin into `<hermes home>/plugins/hermachy`, creates the private binding directories, and, unless `--no-enable` is given, runs `hermes --profile default plugins enable hermachy --no-allow-tool-override`. It does not restart the gateway.

The complete multi-file installation sequence is not transactional. If a later validation fails, an executable, key pair, configuration file, plugin file, or `plugins.enabled` entry written earlier in that phase can remain. Correct the reported error and rerun the same phase, or revoke the relevant key block and remove the partial Hermachy files if abandoning setup.

## Privilege policy

Hermachy does not:

- install or modify sudoers;
- configure passwordless sudo;
- copy model/API credentials;
- enable SSH root login;
- patch Hermes, or start, stop, or reconfigure the Hermes gateway beyond installing and enabling its plugin;
- modify Tailscale Serve or Funnel;
- weaken SSH host-key checking.

If a local task requires administrative access, handle it through the normal interactive security boundary rather than adding unattended sudo rights.

## Revocation

### Revoke Omarchy access to the remote Hermes host

Run on the remote host:

```bash
~/.local/bin/hermachy-admin revoke --side studio
```

This removes only the `omarchy-client` managed block from the remote host's `authorized_keys`.

### Revoke remote execution on Omarchy

Run on Omarchy:

```bash
~/.local/bin/hermachy-admin revoke --side omarchy
```

This removes only the `studio-executor` managed block from Omarchy's `authorized_keys`.

Both revocation commands block **future SSH authentication only**. OpenSSH does not terminate a connection merely because its key was removed from `authorized_keys`.

Bindings outlive the client connection. Revoking `--side studio` stops new
Hermachy connections, but a bound chat continued from Hermes Desktop or another
surface still runs its tools on Omarchy over the execution key. To stop execution
on Omarchy, revoke `--side omarchy` as well. On the remote host, moving
`~/.config/hermachy/server.json` out of place also makes every bound tool call fail
closed, without stopping the shared gateway.

Before treating access as stopped:

1. Revoke the relevant key block.
2. Exit every active Hermachy TUI using that link.
3. Identify and terminate the exact processes of the link on both machines: on Omarchy, the `hermachy` client, its renderer, and its `ssh` channels; on the remote host, the `hermachy-server` bridge processes that sshd started for the client key, and the plugin's SSH and control-master processes to Omarchy, whose control sockets are under `/tmp/hermachy-ssh-*`. Verify process identity before acting. Do not stop the shared gateway or broadly kill unrelated Hermes or SSH sessions; the gateway also serves the profile's other surfaces.
4. Verify no process or connection associated with that link remains.

Then remove the corresponding private/public key pair and installed endpoint if decommissioning permanently. On the remote host, also remove `hermachy` from the default profile's `plugins.enabled`, delete `<hermes home>/plugins/hermachy` and `<hermes home>/hermachy`, and restart the gateway. Previously bound chats then run on the host like any other chat of the profile.

## Compromise response

If Omarchy is lost or suspected compromised:

1. Revoke `--side studio` on the remote host.
2. Terminate any active Hermachy bridge and SSH processes for the link.
3. Remove the remote host's `omarchy-client` public-key block if the admin tool is unavailable.
4. Rotate the Omarchy-to-remote-host key before reconnecting.
5. Review recent chats and actions in the default profile, including configuration, skill, plugin, MCP server, and vault changes made through the gateway, and commands run with `hermes_host`.

If the remote Hermes host, its shared gateway, or its execution key is suspected compromised:

1. Revoke `--side omarchy` on Omarchy.
2. Terminate any active Hermachy execution SSH or control-master processes for the link.
3. Remove Omarchy's `studio-executor` public-key block if needed.
4. Rotate the remote-host-to-Omarchy key.
5. Review the Omarchy user's shell, file, and authentication logs.

## Reporting vulnerabilities

Use the repository's private vulnerability-reporting channel if enabled, or contact the maintainers before publishing exploitable details. Do not include private keys, API keys, `auth.json`, `.env`, unredacted Hermes configuration, Tailscale IPs, or private hostnames.

A useful report includes:

- Hermachy version and source commit;
- Hermes version (`hermes --version` on the remote host) and Hermachy plugin version;
- both operating systems;
- a redacted command and exit status;
- redacted `hermachy --doctor` output;
- the expected and observed security boundary.
