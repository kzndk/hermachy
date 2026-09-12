# Hermachy (`hermachy`)

**Render the native Hermes TUI locally, keep your remote agent connected, and execute commands and tools on Omarchy or the Hermes host as the task requires.**

[![Tests](https://github.com/kzndk/hermachy/actions/workflows/test.yml/badge.svg)](https://github.com/kzndk/hermachy/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Hermachy connects two machines over Tailscale and OpenSSH:

```text
Omarchy workstation
  `hermachy` client + native TUI
       │  SSH JSON-RPC over Tailscale
       ▼
Remote Hermes host
  persistent gateway + Hermes Agent + remote execution
  memory, skills, sessions, models, credentials
       │  dedicated SSH execution connection
       ▼
Omarchy workstation
  terminal, files, patches, searches, builds, code execution
```

The remote host remains the authoritative Hermes “brain.” The agent can choose Omarchy or the Hermes host for each command. Hermachy does not clone a Hermes profile or replace the Hermes TUI. The current pre-alpha launches the remote Hermes `default` profile; do not give an untrusted workstation access to a sensitive profile.

> [!IMPORTANT]
> **Current status: public pre-alpha and fail-closed.** Install the [pinned compatible Hermes runtime](compat/hermes/README.md): a public upstream commit plus the complete **safe-executor capability v2** patch included here. Stock builds without this contract are rejected before the TUI starts. See [Hermes compatibility](#hermes-compatibility).

## What stays where

| Capability or data | Remote Hermes host | Omarchy |
|---|:---:|:---:|
| Native Hermes TUI renderer | — | ✓ |
| Hermes gateway and agent loop | ✓ | — |
| Models and provider credentials | ✓ | — |
| Memory, skills, sessions, and profile state | ✓ | — |
| Shell commands and installed CLI tools | ✓ | ✓ |
| Native file and code tools | — | ✓ |
| File operations, builds and code through shell/Python commands | ✓ | ✓ |
| Hermachy client key | public key only | private key |
| Hermachy execution key | private key | public key only |

Hermachy does not configure passwordless `sudo`, Tailscale Funnel, or a public Internet listener.

## Start here

The complete setup guide covers prerequisites, Tailscale/SSH preparation, the two dedicated keys, installation on both machines, diagnostics, usage, verification, upgrades, and revocation:

### **[Configure Omarchy with a remote Hermes Agent →](docs/OMARCHY_REMOTE_HERMES.md)**

Quick outline:

1. Install Tailscale, OpenSSH, Git, Python 3.9+, and Node.js on Omarchy.
2. [Install the pinned Hermes runtime](compat/hermes/README.md) and configure it on the remote host (Hermes uses Python 3.11).
3. Build Hermachy’s four standalone zipapps and bundled native TUI.
4. Run `hermachy-admin studio-prepare` on the remote host.
5. Run `hermachy-admin omarchy-install` on Omarchy.
6. Run `hermachy-admin studio-finish` on the remote host.
7. Run `hermachy --doctor`, then launch `hermachy` from an Omarchy project directory.

## Build

Version **0.4.1** includes the CLI, connection recovery, explicit execution
targets, and the optional Omarchy desktop app. See [release notes](CHANGELOG.md).

```bash
git clone https://github.com/kzndk/hermachy.git
cd hermachy
python3 scripts/build_zipapps.py
```

The build produces four Python zipapps that use the standard library, plus the
bundled renderer:

```text
dist/hermachy          # Omarchy client
dist/hermachy-server   # forced-command endpoint on the remote Hermes host
dist/hermachy-admin    # install and revoke utility
dist/hermachy-gateway  # local stdio-to-SSH transport
dist/tui/              # renderer, provenance, and license
```

Python 3.9 or newer is required. The Omarchy client also requires Node.js.
The build verifies the bundled renderer and parser against `tui/PROVENANCE.json`.

The renderer and parser are vendored from Hermes
[`53c57871d67ee7d2202861aacc4ea0ef6ef93112`](https://github.com/NousResearch/hermes-agent/commit/53c57871d67ee7d2202861aacc4ea0ef6ef93112)
(2026-09-12). The renderer is built with the upstream lockfile and
`npm run build --workspace ui-tui`; `hermes_cli/_parser.py` is copied verbatim.
This UI revision is separate from the safe-executor runtime recipe below:
updating the renderer does not replace the remote Hermes runtime or its patches.

The [0.4.0 release](https://github.com/kzndk/hermachy/releases/tag/v0.4.0)
provides `hermachy-0.4.0.tar.gz` with all four executables and the required `tui/`
assets. That historical archive predates the compatibility installer; use this
source checkout's `compat/hermes/` directory to prepare Hermes. New bundles
built from this tree include the patch, installer, manifest, and setup docs.
For downloaded bundles, obtain the matching `SHA256SUMS`, verify it with
`sha256sum -c SHA256SUMS` on Linux or `shasum -a 256 -c SHA256SUMS` on macOS, then
follow the [archive installation steps](docs/OMARCHY_REMOTE_HERMES.md#5-prepare-the-hermachy-release-directory).
Keep the executables and `tui/` together until the installer copies them into
their installed locations.
The desktop app is installed from the source checkout using the
[desktop guide](https://github.com/kzndk/hermachy/blob/v0.4.0/desktop/README.md).

To build a bundle from the current source and verify the committed runtime hashes:

```bash
python3 scripts/build_release.py --check
```

Maintainers run it without `--check` after changing the Python runtime to refresh
`release-sha256.json`. The versioned archive and `SHA256SUMS` are written to
`dist/release/`.

The archive is deterministic for the same source tree. A later commit can
produce a different archive with the same version label; use the source at
tag `v0.4.0` when reproducing the historical release.

## Use

### Desktop app for Omarchy

The [Hermachy app](https://github.com/kzndk/hermachy/blob/v0.4.0/desktop/README.md) adds streaming chat, searchable saved
conversations, file and image attachments, and three original animated companions
that react while your agent works. It uses the same CLI configuration and SSH
connection. See the desktop guide for installation, then open **Hermachy** from
the Omarchy launcher or run `hermachy-app --cwd /path/to/project`.

### Terminal interface

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

### Connection recovery and execution targets

Both SSH legs send keepalives every 15 seconds and detect an unresponsive peer
after three unanswered probes. The local gateway reconnects automatically with
backoff up to 15 seconds. A private supervisor keeps the same remote agent alive
for up to one hour without a client attachment. Its acknowledged stream resumes
input and output without executing a submitted RPC twice. Buffers apply
backpressure when full. No additional TCP listener is opened.

The native terminal tool exposes the execution target directly. The agent supplies
`target="omarchy"` or `target="hermes"` for each command. For example, ask it to
inspect your Omarchy desktop, run local project tests, or check services and files
on the Hermes host. Remote commands use the Hermes user's home by default; an
explicit `workdir` belongs to the chosen machine. Each result identifies its
target. The Hermes target supports foreground commands up to 300 seconds and uses Hermes
command approvals. Omarchy retains native background/process support. Native file/code tools retain their Omarchy route; remote
file operations and code run through shell commands on the Hermes target.

Closing the TUI deliberately shuts down its gateway. A remote reboot or gateway
crash requires resuming the saved session with `hermachy --continue`; automatic
transport recovery does not replay commands into a replacement agent. An
interrupted Omarchy execution SSH connection can leave a command's outcome
uncertain; the agent must inspect its result before retrying it. Upgrade both
Hermachy endpoints together to 0.4.0 for these features.

## Hermes compatibility

The supported recipe is Hermes
[`8e0b9819d3be29e1628da4014c8065243402964b`](https://github.com/NousResearch/hermes-agent/commit/8e0b9819d3be29e1628da4014c8065243402964b)
with the bundled [safe-executor v2 patch](compat/hermes/safe-executor-v2.patch).
The [compatibility manifest](compat/hermes/manifest.json) pins the patch SHA-256
and resulting Git tree. No private fork is required.

On the remote host, from this checkout, start with:

```bash
python3 compat/hermes/prepare.py "$HOME/.local/share/hermachy/hermes-safe-executor-v2"
```

Then follow the [runtime installation steps](compat/hermes/README.md) to install
locked dependencies into `venv/`, configure your provider, and verify the source
and capability. Keep this source pinned; arbitrary Hermes updates are not a
supported upgrade path.

Hermachy requires this Python module constant in the remote Hermes runtime:

```python
from tools.environments.ssh import HERMACHY_CONTROL_VERSION
assert HERMACHY_CONTROL_VERSION == "2"
```

It is not an environment variable to set during installation.

Capability v2 includes the local TUI gateway contract and preserves the v1 execution boundary. It is a behavioral contract, not merely a constant. It must guarantee that:

- the process-scoped Hermachy route overrides persistent terminal configuration;
- terminal, file, and code-execution tools use the same Omarchy SSH route;
- remote profile/credential/skill/cache synchronization is disabled;
- sync-back is disabled;
- only Hermachy’s dedicated execution identity is used;
- capability probing and TUI launch use the same pinned Hermes venv and source tree;
- unsafe or malformed routing fails closed.

`hermachy-server` verifies that the installed Hermes runtime declares capability v2 before launch. `hermachy --doctor` then reports the remote host identity, profile, and Hermes version, and validates the Omarchy executor's SSH identity, Linux operating system, and requested working directory. These runtime checks do not independently prove the capability's internal implementation; use the pinned recipe and its contract tests above. If the marker or endpoint checks fail, Hermachy exits safely.

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
