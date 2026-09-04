# Hermes Link Security

Hermes Link deliberately joins two machines into one agent execution boundary. Read this document before installation.

## Fail-closed Hermes compatibility gate

Stock Hermes SSH backends currently synchronize declared credential files, skills, and cache to the remote executor and can synchronize changes back during teardown. Hermes Link does not accept that behavior.

Before launching either the TUI or `--doctor`, `hlink-server` imports the installed Hermes SSH backend in its own runtime and requires `REMOTE_STATE_SYNC_CONTROL_VERSION == 1`. It then sets both `TERMINAL_SYNC_REMOTE_STATE=false` and `HLINK_REQUIRE_NO_REMOTE_STATE_SYNC=1` for the Hermes child process. Missing, mismatched, or broken capability detection stops the launch before Hermes initializes the SSH environment.

Do not remove or bypass this check. A compatible Hermes build is a hard prerequisite.

## Trust model

### Omarchy-to-Studio key

The dedicated `~/.ssh/hlink_to_morpheus` private key stays on Omarchy. Its public key is installed on the Studio with an OpenSSH forced command.

The key:

- may allocate a PTY for the real Hermes TUI;
- may invoke only `hlink-server`;
- may not obtain an ordinary Studio shell;
- may not forward ports, agents, or X11;
- may not run user SSH rc files.

The forced server accepts only `hlink-v1` plus a bounded, URL-safe base64 JSON request. It does not evaluate the request as shell text. Supported requests are new session, continue, resume, and doctor.

**Important:** the forced command limits SSH, not Hermes itself. Anyone holding the Omarchy client key can interact with the full default Morpheus agent and whatever non-terminal tools that profile permits. Protect the Omarchy account and private key accordingly.

### Studio-to-Omarchy key

The dedicated `~/.ssh/hlink_to_omarchy` private key stays on the Studio. Its public key is installed on Omarchy with OpenSSH's `restrict` option.

The key intentionally permits arbitrary non-PTY shell commands as the normal Omarchy user. This is the mechanism used by Hermes's supported SSH terminal backend for terminal, file, patch, search, and code-execution tools.

The key:

- cannot forward ports, agents, or X11;
- cannot allocate a PTY;
- does not receive passwordless sudo;
- has all permissions of the normal Omarchy user for non-interactive commands.

A compromised Studio or stolen Studio execution key therefore exposes data and commands available to that Omarchy user.

## Network boundary

Both SSH connections use Tailscale MagicDNS addresses and are intended to be reachable only inside the tailnet. Hermes Link does not create a Tailscale Serve/Funnel route and does not expose a new TCP listener.

The client and server use:

- `BatchMode=yes`
- `IdentitiesOnly=yes`
- `StrictHostKeyChecking=accept-new`
- an explicit dedicated identity file
- disabled agent and port forwarding
- bounded connection timeouts

`accept-new` provides trust on first use and rejects later host-key changes. Treat a host-key mismatch as a security event; do not bypass it with `StrictHostKeyChecking=no`.

## Secrets

With the no-sync capability active, Hermes model/provider credentials, memory, skills, cache, and session database remain on the Studio. The Omarchy client configuration contains only the Studio hostname, username, port, and local path to its dedicated private key.

The doctor response does not print identity paths or key material.

## File permissions

Hermes Link writes:

- private keys as mode `0600`;
- JSON endpoint configuration as mode `0600`;
- `authorized_keys` as mode `0600`;
- `.ssh` and private config directories as mode `0700`;
- executable zipapps as mode `0755`;
- public keys as mode `0644`.

Server startup rejects a server config or executor private key that is owned by another user or accessible to group/other users.

## Installer behavior

Installer-managed `authorized_keys` sections have exact markers:

```text
# >>> hermes-link:studio-executor >>>
# <<< hermes-link:studio-executor <<<

# >>> hermes-link:omarchy-client >>>
# <<< hermes-link:omarchy-client <<<
```

Updates are atomic and idempotent. Content outside the matching block is preserved. Malformed or duplicate markers cause a fail-closed error rather than a rewrite.

Only Ed25519 public keys with a structurally valid OpenSSH key blob are accepted.

## Privilege policy

Hermes Link does not:

- install or modify sudoers;
- configure passwordless sudo;
- copy model/API credentials;
- enable SSH root login;
- modify the Hermes gateway;
- modify Tailscale Serve or Funnel;
- weaken SSH host-key checking.

If a local task requires administrative access, handle it through the ordinary interactive security boundary rather than adding unattended sudo rights.

## Revocation

### Revoke Omarchy access to Morpheus

Run on the Studio:

```bash
~/.local/bin/hlink-admin revoke --side studio
```

This removes only the `omarchy-client` managed block from the Studio's `authorized_keys`.

### Revoke Studio execution on Omarchy

Run on Omarchy:

```bash
~/.local/bin/hlink-admin revoke --side omarchy
```

This removes only the `studio-executor` managed block from Omarchy's `authorized_keys`.

Revoke first, then remove the corresponding private/public key pair and installed endpoint if decommissioning permanently.

## Compromise response

If Omarchy is lost or suspected compromised:

1. Revoke `--side studio` on Morpheus.
2. Remove the Studio's `omarchy-client` public-key block if the admin tool is unavailable.
3. Rotate the Omarchy-to-Studio key before reconnecting.
4. Review recent Hermes sessions and actions.

If the Studio or its execution key is suspected compromised:

1. Revoke `--side omarchy` on Omarchy.
2. Remove the Omarchy `studio-executor` public-key block if needed.
3. Rotate the Studio-to-Omarchy key.
4. Review the Omarchy user's shell, file, and authentication logs.

## Reporting

Do not include private keys, API keys, `auth.json`, `.env`, or unredacted Hermes configuration in a report. A useful report includes Hermes Link version, operating systems, the two public hostnames, command exit status, and redacted doctor output.
