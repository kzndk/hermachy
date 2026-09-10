# Hermachy Security

Hermachy deliberately joins a remote Hermes host and an Omarchy workstation into one agent execution boundary. Read this document before installation.

## Fail-closed Hermes compatibility gate

Some Hermes SSH backends synchronize declared credential files, skills, and cache to the remote executor and can synchronize changes back during teardown. Hermachy does not accept that behavior.

Before launching either the TUI or `--doctor`, `hermachy-server` imports the installed Hermes SSH backend and requires `HERMACHY_CONTROL_VERSION == "2"`. Capability probing, version diagnostics, and the TUI launch all use the same `hermes_source_root/venv/bin/python`, with `PYTHONPATH` pinned to that source root; an independently configured wrapper cannot select a different Hermes installation. Hermachy then starts Hermes with `TERMINAL_SYNC_REMOTE_STATE=false` and `HERMACHY_REQUIRE_SAFE_EXECUTOR=1`.

Safe-executor capability v2 must guarantee all of the following:

- the process-scoped Hermachy route wins over `.env`, `config.yaml`, managed configuration, session restoration, and cached environments;
- terminal, file, and code-execution tools use the same Omarchy SSH route;
- outbound profile/credential/skill/cache synchronization is disabled;
- sync-back is disabled;
- only Hermachy's dedicated execution identity is used;
- malformed, incomplete, or contradictory controls fail closed.

A module constant alone is not a valid implementation. Missing, mismatched, or broken capability detection stops the launch before Hermes initializes an SSH environment. Do not remove or bypass this check.

Use the [pinned public source and bundled patch](compat/hermes/README.md).
Its manifest and preparation helper verify the source revision, patch checksum,
and resulting tree; its regression tests exercise the contract above. Keep this
runtime pinned when upgrading Hermachy.

## Trust model

### Omarchy-to-remote-host key

The dedicated `~/.ssh/hermachy_to_morpheus` private key stays on Omarchy. The filename is a legacy default; it works with any remote Hermes host. Only its public key is installed on the remote host with an OpenSSH forced command.

The key:

- may allocate a PTY for the real Hermes TUI;
- may invoke only `hermachy-server`;
- may not obtain an ordinary remote-host shell;
- may not forward ports, agents, or X11;
- may not run user SSH rc files.

The forced server accepts only `hermachy-v1` plus a bounded, URL-safe base64 JSON request. It does not evaluate the request as shell text. Supported requests are new session, continue, resume, and doctor.

**Important:** the forced command limits SSH, not Hermes itself. Anyone holding the Omarchy client key can interact with the configured remote Hermes profile and whatever non-terminal tools that profile permits. Protect the Omarchy account and private key accordingly.

### Remote-host-to-Omarchy key

The dedicated `~/.ssh/hermachy_to_omarchy` private key stays on the remote Hermes host. Only its public key is installed on Omarchy with OpenSSH's `restrict` option.

The key intentionally permits arbitrary non-PTY shell commands as the normal Omarchy user. This is the mechanism used by Hermes's protected SSH terminal backend for terminal, file, patch, search, and code-execution tools.

The key:

- cannot forward ports, agents, or X11;
- cannot allocate a PTY;
- does not receive passwordless sudo;
- has the normal Omarchy user's permissions for non-interactive commands.

A compromised remote host or stolen execution key therefore exposes data and commands available to that Omarchy user.

## Network boundary

Both SSH connections use Tailscale addresses and are intended to be reachable only inside the tailnet. Hermachy does not create a Tailscale Serve/Funnel route and does not expose a new TCP listener.

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
- bounded connection timeouts

Safe-executor capability v2 applies equivalent dedicated-identity isolation to Hermes's outbound Omarchy connection.

`StrictHostKeyChecking=yes` requires the exact destination key to be recorded
before the first Hermachy connection. Verify both destination fingerprints
out-of-band, populate each user's `known_hosts`, and treat either a missing or
mismatched key as a security event. Never work around it with
`StrictHostKeyChecking=no`.

## Persistent gateway and explicit host execution

Hermachy 0.4.0 keeps the gateway in a detached supervisor on the Hermes host.
Private Unix sockets under `/tmp/hermachy-<uid>/` accept attachments through the
existing forced-command SSH endpoint. Connection IDs are random 128-bit values.
Durable creation records under `~/.local/state/hermachy/connections/` prevent a
terminated gateway from being recreated with the same stream state, including
after a reboot. The directories are owner-only. Socket-side logs are private.
The stream keeps bounded unacknowledged data in memory and deduplicates replayed
records. The supervisor expires after one hour without an attachment. Exit from
the TUI requests shutdown; loss of SSH alone does not stop the running agent.

The Hermachy-only `terminal(target="hermes")` tool explicitly permits commands on either
machine. The `hermes` target executes as the remote Hermes user using the Hermes
local environment and command approval checks. It therefore has access to that
user's remote files and programs. This is an intentional addition to the trust
boundary; it does not change the safe-executor contract of the native terminal,
file or code tools, whose default route remains Omarchy. The extension never toggles
the protected process route or automatically copies profile state between hosts.
Routing is explicit per call, with no fallback to the other machine on failure.

## Secrets and authoritative state

With safe-executor capability v2 active, these stay on the remote Hermes host:

- model and provider credentials;
- `.env`, `auth.json`, and credential pools;
- memory and user profile;
- skills and cache;
- session database and transcripts;
- the authoritative Hermes configuration.

The Omarchy client configuration contains only the remote hostname, username, port, and local path to its dedicated private key. The doctor response does not print identity paths or key material.

## File permissions

Hermachy writes:

- private keys as mode `0600`;
- JSON endpoint configuration as mode `0600`;
- `authorized_keys` as mode `0600`;
- `.ssh` and private config directories as mode `0700`;
- executable zipapps as mode `0755`;
- public keys as mode `0644`.

Server startup rejects a server configuration or execution private key owned by another user or accessible to group/other users.

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

The complete multi-file installation sequence is not transactional. If a later validation fails, an executable, key pair, or configuration file written earlier in that phase can remain. Correct the reported error and rerun the same phase, or revoke the relevant key block and remove the partial Hermachy files if abandoning setup.

## Privilege policy

Hermachy does not:

- install or modify sudoers;
- configure passwordless sudo;
- copy model/API credentials;
- enable SSH root login;
- modify the Hermes gateway;
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

Both revocation commands block **future SSH authentication only**. OpenSSH does not terminate a connection merely because its key was removed from `authorized_keys`. Before treating access as stopped:

1. Revoke the relevant key block.
2. Exit every active Hermachy TUI using that link.
3. Identify and terminate the exact Hermachy supervisor (`hermachy.persistent`), its Hermes gateway and associated SSH/control-master processes on both machines. The supervisor owns the gateway process group and terminates it when stopped. Connection creation records contain supervisor PIDs; verify process identity before acting on an old record. Do not broadly kill unrelated Hermes or SSH sessions.
4. Verify no process or connection associated with that link remains.

Then remove the corresponding private/public key pair and installed endpoint if decommissioning permanently.

## Compromise response

If Omarchy is lost or suspected compromised:

1. Revoke `--side studio` on the remote host.
2. Terminate any active Hermachy-started Hermes and SSH processes for the link.
3. Remove the remote host's `omarchy-client` public-key block if the admin tool is unavailable.
4. Rotate the Omarchy-to-remote-host key before reconnecting.
5. Review recent Hermes sessions and actions.

If the remote Hermes host or its execution key is suspected compromised:

1. Revoke `--side omarchy` on Omarchy.
2. Terminate any active Hermachy execution SSH or control-master processes for the link.
3. Remove Omarchy's `studio-executor` public-key block if needed.
4. Rotate the remote-host-to-Omarchy key.
5. Review the Omarchy user's shell, file, and authentication logs.

## Reporting vulnerabilities

Use the repository's private vulnerability-reporting channel if enabled, or contact the maintainers before publishing exploitable details. Do not include private keys, API keys, `auth.json`, `.env`, unredacted Hermes configuration, Tailscale IPs, or private hostnames.

A useful report includes:

- Hermachy version and source commit;
- Hermes version and safe-executor capability version;
- both operating systems;
- a redacted command and exit status;
- redacted `hermachy --doctor` output;
- the expected and observed security boundary.
