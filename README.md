# Hermachy (`hermachy`)

**Render the native Hermes TUI locally, connect to a remote Hermes Agent, and execute terminal, file, and code tools on your Omarchy machine.**

[![Tests](https://github.com/kzndk/hermachy/actions/workflows/test.yml/badge.svg)](https://github.com/kzndk/hermachy/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Hermachy connects two machines over Tailscale and OpenSSH:

```text
Omarchy workstation
  `hermachy` client + native TUI
       │  SSH JSON-RPC over Tailscale
       ▼
Remote Hermes host
  `tui_gateway.entry` + Hermes Agent
  memory, skills, sessions, models, credentials
       │  dedicated SSH execution connection
       ▼
Omarchy workstation
  terminal, files, patches, searches, builds, code execution
```

The remote host remains the authoritative Hermes “brain.” Omarchy is the execution machine. Hermachy does not clone a Hermes profile or replace the Hermes TUI. The current pre-alpha launches the remote Hermes `default` profile; do not give an untrusted workstation access to a sensitive profile.

> [!IMPORTANT]
> **Current status: public pre-alpha and fail-closed.** Hermachy requires a Hermes build that implements **safe-executor capability v2**. Released Hermes versions that can override the requested route, copy profile state into an SSH backend, or use ambient SSH identities are rejected before the TUI starts. Do not bypass this gate. See [Hermes compatibility](#hermes-compatibility).

## What stays where

| Capability or data | Remote Hermes host | Omarchy |
|---|:---:|:---:|
| Real Hermes TUI and agent loop | ✓ | — |
| Models and provider credentials | ✓ | — |
| Memory, skills, sessions, and profile state | ✓ | — |
| Terminal commands | — | ✓ |
| File read/write/search/patch | — | ✓ |
| Builds, tests, and code execution | — | ✓ |
| Hermachy client key | public key only | private key |
| Hermachy execution key | private key | public key only |

Hermachy does not configure passwordless `sudo`, Tailscale Funnel, or a public Internet listener.

## Start here

The complete setup guide covers prerequisites, Tailscale/SSH preparation, the two dedicated keys, installation on both machines, diagnostics, usage, verification, upgrades, and revocation:

### **[Configure Omarchy with a remote Hermes Agent →](docs/OMARCHY_REMOTE_HERMES.md)**

Quick outline:

1. Install Tailscale, OpenSSH, Git, and Python 3.9+, and Node.js on Omarchy.
2. Install and configure Hermes on the remote host.
3. Build Hermachy’s four standalone zipapps and bundled native TUI.
4. Run `hermachy-admin studio-prepare` on the remote host.
5. Run `hermachy-admin omarchy-install` on Omarchy.
6. Run `hermachy-admin studio-finish` on the remote host.
7. Run `hermachy --doctor`, then launch `hermachy` from an Omarchy project directory.

## Build

```bash
git clone https://github.com/kzndk/hermachy.git
cd hermachy
python3 scripts/build_zipapps.py
```

The reproducible, dependency-free runtime artifacts are:

```text
dist/hermachy          # Omarchy client
dist/hermachy-server   # forced-command endpoint on the remote Hermes host
dist/hermachy-admin    # install and revoke utility
dist/hermachy-gateway  # local stdio-to-SSH transport
dist/tui/           # renderer, provenance, and license
```

Python 3.9 or newer is required. The Omarchy client also requires Node.js.
The build verifies the bundled renderer and parser against `tui/PROVENANCE.json`.

## Use

After completing the setup guide, open a terminal on Omarchy and run:

```bash
cd ~/path/to/your/project
hermachy
```

Common session commands:

```bash
hermachy --doctor
hermachy --continue
hermachy --continue "session name"
hermachy --resume "session-id-or-title"
hermachy --cwd /absolute/omarchy/path
```

The native TUI renders locally; the remote Hermes gateway owns the agent and sessions. The supplied Omarchy directory is the execution working directory. Startup checks the remote runtime and executor before launching the renderer.

## Hermes compatibility

Hermachy requires the remote Hermes runtime to export:

```text
HERMACHY_CONTROL_VERSION=2
```

Capability v2 includes the local TUI gateway contract and preserves the v1 execution boundary. It is a behavioral contract, not merely a constant. It must guarantee that:

- the process-scoped Hermachy route overrides persistent terminal configuration;
- terminal, file, and code-execution tools use the same Omarchy SSH route;
- remote profile/credential/skill/cache synchronization is disabled;
- sync-back is disabled;
- only Hermachy’s dedicated execution identity is used;
- capability probing and TUI launch use the same pinned Hermes venv and source tree;
- unsafe or malformed routing fails closed.

`hermachy-server` verifies that the installed Hermes runtime declares capability v2 before launch. `hermachy --doctor` then reports the remote host identity, profile, and Hermes version, and validates the Omarchy executor's SSH identity, Linux operating system, and requested working directory. These runtime checks do not independently prove the capability's internal implementation, so use only a tested compatible Hermes build. If the marker or endpoint checks fail, Hermachy exits safely. The project will publish a tested compatible-version reference when the upstream capability is available; until then, Hermachy is for developers testing a compatible Hermes build.

## Security

Read [SECURITY.md](SECURITY.md) before installation. The important trust decision is that:

- the Omarchy client key can start the configured remote Hermes profile; and
- the remote execution key can run non-interactive commands as the normal Omarchy user.

Both keys are dedicated Ed25519 identities. The client ignores ambient SSH configuration with `ssh -F /dev/null`, uses a forced command on the remote host, disables forwarding, and never grants passwordless sudo.

Block future SSH authentication (this does not terminate sessions that are already connected):

```bash
# On the remote Hermes host: block Omarchy from launching Hermachy
hermachy-admin revoke --side studio

# On Omarchy: block the remote Hermes host from executing locally
hermachy-admin revoke --side omarchy
```

After revoking both sides, close every active Hermachy TUI and terminate its linked Hermes and SSH processes before treating access as stopped. See [SECURITY.md](SECURITY.md) for the active-session procedure.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests scripts
python3 scripts/build_zipapps.py
./dist/hermachy --version
./dist/hermachy-server --version
./dist/hermachy-admin --help
```

## Project

- Repository: https://github.com/kzndk/hermachy
- Issues: https://github.com/kzndk/hermachy/issues
- License: [MIT](LICENSE)
