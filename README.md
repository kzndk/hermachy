# Hermachy (`hermachy`)

**Render the native Hermes TUI locally, keep your remote agent connected, and execute commands and tools on Omarchy or the Hermes host as the task requires.**

[![Tests](https://github.com/kzndk/hermachy/actions/workflows/test.yml/badge.svg)](https://github.com/kzndk/hermachy/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Hermachy connects two machines over Tailscale and OpenSSH:

```text
Omarchy workstation
  `hermachy` client + native TUI + loopback relay
       │  restricted SSH channel over Tailscale (forced command)
       ▼
Remote Hermes host
  hermachy-server bridge ──► default profile's shared Hermes gateway
  memory, skills, sessions, models, credentials
  Hermachy plugin: routes the tools of Hermachy chats
       │  dedicated SSH execution connection
       ▼
Omarchy workstation
  terminal, processes, files, patches, searches, builds, code execution
```

The remote host remains the authoritative Hermes “brain.” Hermachy chats live in the remote host's **default profile**: the same chats, memory, skills, and session list as Hermes Desktop and the profile's other surfaces. Only chats that Hermachy created or resumed are bound to Omarchy. Their terminal, process, file, and code tools run there, and the agent can choose the `hermes_host` tool to run a command on the Hermes host. Unbound chats behave exactly as without Hermachy. Hermachy does not clone a Hermes profile or replace the Hermes TUI. Do not give an untrusted workstation access to a sensitive profile.

> [!IMPORTANT]
> **Current status: public pre-alpha and fail-closed.** Hermachy 0.6.0 requires stock Hermes Agent **v0.23.1 or later** on the remote host, with the default profile's gateway running as its normal service. It installs a Hermachy plugin into that profile; no patched Hermes runtime is used. A tool call of a Hermachy chat whose Omarchy route cannot be established is refused and never falls back to the host. See [Hermes compatibility](#hermes-compatibility).

## What stays where

| Capability or data | Remote Hermes host | Omarchy |
|---|:---:|:---:|
| Native Hermes TUI renderer | — | ✓ |
| Hermes gateway, agent loop, and Hermachy plugin | ✓ | — |
| Models and provider credentials | ✓ | — |
| Memory, skills, sessions, and profile state | ✓ | — |
| Hermachy session bindings | ✓ | — |
| Shell commands and installed CLI tools | ✓ (`hermes_host`) | ✓ |
| Terminal, process, file, and code tools of Hermachy chats | — | ✓ |
| File operations, builds and code through shell/Python commands | ✓ | ✓ |
| Hermachy client key | public key only | private key |
| Hermachy execution key | private key | public key only |

Hermachy does not configure passwordless `sudo`, Tailscale Funnel, or a public Internet listener. The client's only listener is a loopback relay on Omarchy.

## Start here

The complete setup guide covers prerequisites, Tailscale/SSH preparation, the two dedicated keys, installation on both machines, diagnostics, usage, verification, upgrades, and revocation:

### **[Configure Omarchy with a remote Hermes Agent →](docs/OMARCHY_REMOTE_HERMES.md)**

Quick outline:

1. Install Tailscale, OpenSSH, Git, Python 3.9+, and Node.js on Omarchy.
2. Install and configure Hermes Agent v0.23.1 or later on the remote host, with the default profile's gateway running as a service.
3. Build Hermachy's four standalone zipapps, bundled native TUI, and Hermes plugin, or verify a release archive.
4. Run `hermachy-admin studio-prepare` on the remote host.
5. Run `hermachy-admin omarchy-install` on Omarchy.
6. Run `hermachy-admin studio-finish` on the remote host, then restart its gateway once with `hermes gateway restart`.
7. Run `hermachy --doctor`, then launch `hermachy` from an Omarchy project directory.

## Build

Version **0.6.0** moves Hermachy onto the shared gateway of Hermes **0.23.1**:
Hermachy chats live in the host's default profile, and a Hermes plugin routes
their tools to Omarchy. See [release notes](CHANGELOG.md) and the
[0.6 design note](docs/design/SHARED_GATEWAY_0.6.md) for the architecture and
the reasons for it.

```bash
# From this 0.6.0 source checkout:
python3 scripts/build_zipapps.py
```

The build produces four Python zipapps that use the standard library, plus the
bundled renderer and the Hermes plugin:

```text
dist/hermachy          # Omarchy client, native TUI launcher, and loopback relay
dist/hermachy-server   # forced-command bridge on the remote Hermes host
dist/hermachy-admin    # install and revoke utility
dist/hermachy-exec     # workstation SSH entry point and graphical administration
dist/tui/              # renderer, clipboard helper, patches, provenance, and license
dist/plugin/hermachy/  # Hermes plugin for the remote host's default profile
```

Python 3.9 or newer is required. The Omarchy client also requires Node.js.
The build verifies the bundled renderer, clipboard helper, patches, and parser
against `tui/PROVENANCE.json`.

The renderer and parser are vendored from Hermes
[`3b421578588243f920886e6fe79a6faa87ea026e`](https://github.com/NousResearch/hermes-agent/commit/3b421578588243f920886e6fe79a6faa87ea026e)
(Hermes 0.23.1, release `v0.23.1`). The renderer includes Hermachy's
[activity emoji patch](tui/activity-emojis.patch), which adds the familiar tool icons
to skill, browser, terminal, and other activity labels. Gateway-supplied connector
labels keep their own icons. The [Omarchy theme patch](tui/omarchy-theme.patch)
makes the local renderer follow the workstation's active palette. To rebuild,
apply both patches to that Hermes revision with
`git apply /path/to/hermachy/tui/activity-emojis.patch /path/to/hermachy/tui/omarchy-theme.patch`.
Then build with Hermes's pinned pnpm 12.15.0 (upstream no longer supports `npm ci`):

```bash
source ./activate
pnpm install --frozen-lockfile --filter 'hermes-tui...'
cd ui-tui && pnpm run build
```

Copy `ui-tui/dist/entry.js` to Hermachy's `tui/entry.mjs`,
`ui-tui/scripts/clipboard_image.py` to `tui/scripts/clipboard_image.py`, and
`hermes_cli/_parser.py` verbatim to `src/hermachy/hermes_parser.py`. Refresh their
SHA-256 values in `tui/PROVENANCE.json`. Both patch checksums are also recorded and
checked at build time. The standalone client adapts the parser’s config-path help
to describe the remote profile.

The bridge forwards only gateway methods declared by the same Hermes revision.
When moving to another revision, regenerate `src/hermachy/attach_methods.py` with
`python3 scripts/update_attach_methods.py /path/to/hermes-agent` and review the
bridge policy in `src/hermachy/attach_policy.py` for every new method. The
renderer, parser, and bridge allowlist use the same release. Upgrade the client,
server, and plugin together.

The release build produces `hermachy-0.6.0.tar.gz` with all four executables, the
required `tui/` assets, the `plugin/hermachy/` plugin, `release-sha256.json`, and
the setup docs. Transfer its matching `SHA256SUMS`, verify it with
`sha256sum -c SHA256SUMS` on Linux or `shasum -a 256 -c SHA256SUMS` on macOS, then
follow the [archive installation steps](docs/OMARCHY_REMOTE_HERMES.md#5-prepare-the-hermachy-release-directory).
Keep the executables, `tui/`, and `plugin/` together until the installer copies
them into their installed locations.

To build a bundle from the current source and verify the committed release hashes:

```bash
python3 scripts/build_release.py --check
```

Maintainers run it without `--check` after changing any shipped file (Python
sources, renderer assets, or plugin) to refresh `release-sha256.json`. The
versioned archive and `SHA256SUMS` are written to `dist/release/`.

The archive is deterministic for the same source tree. A later commit can
produce a different archive with the same version label; keep the matching
source and checksums.

## Use

### Terminal interface

After completing the setup guide, open a terminal on Omarchy and run:

```bash
cd ~/path/to/your/project
hermachy
```

Common session commands:

```bash
hermachy --doctor
hermachy --native                  # native terminal scrollback
hermachy --continue
hermachy --continue "session name"
hermachy --resume "session-id-or-title"
hermachy --cwd /absolute/omarchy/path
```

The native TUI renders locally and dials a loopback relay that `hermachy` starts
on `127.0.0.1` (ephemeral port, secret path). Each dial becomes one restricted SSH
channel to the host's bridge, which attaches to the default profile's running
gateway. The supplied Omarchy directory is the chat's execution working directory.
Startup checks the host's Hermes gateway and plugin and the Omarchy executor
before launching the renderer. It refuses a host that reports a different
Hermachy version or transport.

`hermachy --continue` picks the most recent Hermachy chat bound to the same
Omarchy directory, falling back to the profile's most recent chat.
`--continue "name"` and `--resume SELECTOR` resolve a title or session ID through
the host. Resuming any chat of the profile in Hermachy binds it to the current
Omarchy directory.

Hermes launch options ride the local TUI environment and apply to new chats:
`--model`, `--provider`, `--toolsets`, `--skills`, `--max-turns`, `--yolo`, `--checkpoints`,
`--accept-hooks`, `--pass-session-id`, `--ignore-rules`, `--verbose`, and
`--quiet`. `--reasoning` is added to new chats. `--safe-mode` and
`--ignore-user-config` are refused: those sessions skip plugins, and with them
the workstation route.

Inline image previews, `@path` completion, and host file attachments are
unavailable in Hermachy: they would read paths on the Hermes host. Images from
Omarchy (`--image`, a dropped image path, or a pasted clipboard image) are
uploaded as bytes instead.

Project instructions (`.hermes.md`, inherited `AGENTS.md`, `CLAUDE.md`, and
Cursor rules) are read from that Omarchy workspace with
`hermachy-exec --context-files`. They follow Hermes's normal priority and scanning
and are added to Hermachy chats in a prompt section with the routing rules,
capped at 4000 characters in total; longer instructions are truncated with a
note to read the rest on the workstation. The chat's host-side working
directory is an empty Hermachy workspace marker, not a project. `SOUL.md`,
memory, and skills continue to come from the remote Hermes profile. After
upgrading, reopen Hermachy and start a new chat to rebuild the prompt; existing
conversations keep their cached instructions.

Tool activity shows emojis such as 📚 for skills, 🌐 for browser navigation,
💻 for terminal commands, and 🔍 for web searches. They appear while a call is
being prepared, while it runs, and in its completed activity row. Reopen Hermachy
after a renderer upgrade to load the updated display.

Hermachy follows Omarchy's active colors at launch and while running. Changing
the desktop theme updates the TUI within a second, preserving the current chat
and input. It reads `$XDG_STATE_HOME/omarchy/current/theme/colors.toml`
(normally `~/.local/state/omarchy/current/theme/colors.toml`); remote skins retain
their branding while the local palette supplies the colors. Without an Omarchy
palette, the normal Hermes skin applies.

### Connection recovery and execution targets

Both SSH legs send keepalives every 15 seconds and detect an unresponsive peer
after three unanswered probes. When the client's channel drops, the TUI redials
the loopback relay by itself. Each redial opens a fresh SSH channel and a new
single-use gateway ticket and keeps the same connection ID, so the chat stays
bound to the same Omarchy directory. The only new TCP listener is the client's
loopback relay.

Chats live in the shared gateway, not in the client. Closing the TUI ends only its
SSH channel and bridge; the gateway keeps running for the profile's other
surfaces. After a gateway restart or a remote reboot, reopen the chat with
`hermachy --continue` or `hermachy --resume`.

In a Hermachy chat, the terminal, process, file (`read_file`, `write_file`,
`patch`, `search_files`), and `execute_code` tools run on Omarchy, starting in the
launch directory. For example, ask the agent to inspect your Omarchy desktop or
run local project tests. The `hermes_host` tool runs a foreground shell command on
the Hermes host instead, for example to check services and files there. It
replaces 0.5.x's `target` parameter and exists only in Hermachy chats. Host
commands run as the remote Hermes user with Hermes command approvals, for up to
300 seconds; `workdir` must be an absolute host path and defaults to the Hermes
user's home. Each `hermes_host` result identifies its target. Remote file
operations and code run through `hermes_host` shell commands.

A binding belongs to the chat, not to the window: a Hermachy chat continued in
Hermes Desktop or another surface still runs its tools on Omarchy. Chats that
Hermachy never created or resumed are untouched.

Routing fails closed. If a Hermachy chat's binding, server configuration, or
execution key is missing or invalid, or the workstation environment cannot be
opened, the tool call returns an error and nothing runs; there is no fallback to
the other machine. An interrupted or timed-out Omarchy execution SSH connection
returns a result marked `hermachy_outcome_unknown`: the command's outcome is
uncertain, and the agent must inspect its result before retrying it. Such results
never enter Hermes's automatic command retry loop. Upgrade the Hermachy client,
server, and plugin together to 0.6.0, on Hermes v0.23.1 or later.

### Desktop and system access

The dedicated execution key runs commands through `hermachy-exec`. Each command
gets the workstation's current Hyprland, Wayland, D-Bus and command paths from
its local user session. Desktop connection settings are refreshed after a
compositor restart and excluded from Hermes's persistent shell snapshots.
Ordinary terminal, file and code tools retain the workstation user's access.
Native file paths and `~` expansion use Omarchy; the remote host's filesystem
cannot rewrite their destinations. Host-side Hermes checkpoints are unavailable
for these workstation paths; use version control on the workstation.

For administration, the agent uses `hermachy-exec --admin COMMAND [ARG ...]` on
Omarchy. Polkit shows the workstation's password dialog; passwords never enter
the chat or SSH tool stream. The command runs in a transient user service so
authentication reaches the graphical session. Cancelling the caller stops the
service, including any pending authentication. Shell syntax requires an explicit
shell, for example `hermachy-exec --admin /bin/bash -c 'systemctl restart example'`.

`hermachy --doctor` reports desktop, Wayland, user bus, executor entry point and
administration availability separately from transport health. A missing desktop
produces a warning and leaves headless terminal work available. Administrative
availability reports the authentication path; it does not pre-authorize root
access. Re-run the Omarchy installation phase when upgrading so its SSH key uses
the new entry point.

## Hermes compatibility

Hermachy 0.6.0 supports stock, unpatched Hermes Agent **v0.23.1 or later**
([release `v0.23.1`](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.1),
[`3b421578588243f920886e6fe79a6faa87ea026e`](https://github.com/NousResearch/hermes-agent/commit/3b421578588243f920886e6fe79a6faa87ea026e))
on the remote host. No private fork or patch is required.

Hermes 0.23 allows only one gateway per profile, and its Ink TUI attaches to that
gateway over a ticketed WebSocket. Hermachy 0.5.x's private, patched runtime
(safe-executor v2) cannot run under that model and has been removed. The 0.5.2
runtime recipe and its guide remain in the repository history at commit
`bedb4ad`.

Hermachy 0.6 instead attaches to the default profile's running gateway and
enforces its boundary in two places:

- the forced-command bridge, `hermachy-server`, forwards only allowed gateway
  methods and rewrites `session.create`; and
- the Hermachy plugin in the default profile routes the tools of bound chats to
  Omarchy and fails closed.

[SECURITY.md](SECURITY.md) describes both.

The remote host must provide:

- Hermes v0.23.1 or later as its main installation; the plugin needs the
  terminal-environment provider hook that v0.23.1 introduced;
- the default profile's gateway running as its normal launchd or systemd
  service; the bridge discovers it with `hermes --profile default gateway attach --json`
  and never starts a gateway under sshd;
- the Hermachy plugin installed in `<hermes home>/plugins/hermachy` at the same
  version as the client and server, enabled with
  `hermes plugins enable hermachy --no-allow-tool-override`, and loaded by
  restarting the gateway once.

`hermachy --doctor` checks the local TUI bundle and Node.js; on the host,
`hermes --version`, a real attach and `ping` to the default profile's gateway, and
the installed and enabled plugin version; and the Omarchy executor's SSH identity,
Linux operating system, requested working directory, and desktop access. These
checks do not show which plugins the running gateway has loaded. After installing
or upgrading the plugin, restart the gateway and
[verify the execution location](docs/OMARCHY_REMOTE_HERMES.md#11-verify-execution-location).
If a check fails, Hermachy exits safely.

Treat the client, server, plugin, and the host's Hermes version as one
compatibility set. After a `hermes update` on the host that changes the gateway
protocol, Hermachy refuses to attach until a matching Hermachy release is
installed. Gateway methods added by a newer Hermes stay refused until a Hermachy
release reviews them.

## Security

Read [SECURITY.md](SECURITY.md) before installation. The important trust decision is that:

- the Omarchy client key can use the remote host's default Hermes profile, including its chats, memory, skills, and agent, and run approved commands on the host through `hermes_host`; and
- the remote execution key, which the Hermachy plugin uses inside the shared gateway process, can run non-interactive commands as the normal Omarchy user.

Both keys are dedicated Ed25519 identities. The client ignores ambient SSH configuration with `ssh -F /dev/null`, uses a forced command on the remote host, disables forwarding, and never grants passwordless sudo.

Block future SSH authentication (this does not terminate sessions that are already connected):

```bash
# On the remote Hermes host: block Omarchy from launching Hermachy
hermachy-admin revoke --side studio

# On Omarchy: block the remote Hermes host from executing locally
hermachy-admin revoke --side omarchy
```

Bound chats stay bound. Until the Omarchy side is revoked, a Hermachy chat continued from another surface of the profile still runs its tools on Omarchy. After revoking both sides, close every active Hermachy TUI and terminate its linked bridge and SSH processes before treating access as stopped. See [SECURITY.md](SECURITY.md) for the active-session procedure.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests scripts
python3 scripts/build_zipapps.py
./dist/hermachy --version
./dist/hermachy-server --version
./dist/hermachy-admin --help
./dist/hermachy-exec --version
```

The plugin tests in `tests/integration/` need a Hermes v0.23.1 or later source
checkout with its built `venv/`:

```bash
PYTHONPATH=src:/path/to/hermes-agent /path/to/hermes-agent/venv/bin/python \
  -m unittest discover -s tests/integration -v
```

## Project

- Repository: https://github.com/kzndk/hermachy
- Issues: https://github.com/kzndk/hermachy/issues
- License: [MIT](LICENSE)
