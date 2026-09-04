# Hermes Link

**The real Hermes TUI, backed by Morpheus, with commands executed on your local Omarchy machine.**

Hermes Link keeps one authoritative Hermes identity on the Mac Studio while making an Omarchy workstation the active terminal and filesystem backend.

> **Pre-alpha / fail-closed:** Released Hermes builds currently synchronize credential files, skills, and cache into SSH backends. Hermes Link will **refuse to launch** unless the installed Hermes build exposes no-sync capability v1 and honors `TERMINAL_SYNC_REMOTE_STATE=false`. Do not bypass this gate. No public release is provided yet.

```text
Omarchy `hlink`
  → Tailscale SSH
  → real `hermes --tui` on Morpheus
  → Hermes SSH backend
  → commands/files/code on Omarchy
```

## What you get

- The exact Hermes TUI: same rendering, keys, streaming, overlays, themes, mouse behavior, and terminal resize handling.
- Morpheus's existing memory, skills, sessions, model credentials, and profile.
- Terminal commands, file operations, patches, searches, builds, tests, and code execution on Omarchy.
- With a compatible no-sync Hermes build, no API/model credential, skill tree, cache, or SQLite profile is copied to Omarchy.
- Process-local routing that does not change normal Morpheus, Hermes-All, gateway, cron, or Tailscale Serve configuration.

## Execution boundary

| Capability | Runs/lives on |
|---|---|
| Hermes TUI and agent loop | Mac Studio / Morpheus |
| Memory, skills, sessions, credentials | Mac Studio / Morpheus |
| Terminal commands | Omarchy |
| File read/write/search/patch | Omarchy |
| Code execution and project builds | Omarchy |
| Gateway, messaging, cron | Mac Studio / Morpheus |
| Browser and Computer Use | Mac Studio initially |

## Requirements

### Omarchy

```bash
sudo pacman -S --needed tailscale openssh python
sudo systemctl enable --now tailscaled sshd
sudo tailscale up
```

Complete the Tailscale login. The machine must be online in the same tailnet as Morpheus. Python 3.9 or newer is required; current Omarchy Python satisfies this.

### Mac Studio

- Working `hermes --tui`
- Hermes SSH no-sync capability v1 (`REMOTE_STATE_SYNC_CONTROL_VERSION == 1`)
- OpenSSH Remote Login enabled
- Tailscale online
- Python 3.9 or newer

## Build and test

From this repository:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 scripts/build_zipapps.py
```

Artifacts:

```text
dist/hlink
dist/hlink-server
dist/hlink-admin
```

They are standalone Python zipapps with no third-party runtime dependencies.

## Installation

Installation deliberately has three phases so private keys never cross machines.

### Phase 1 — prepare Morpheus

On the Mac Studio:

```bash
python3 scripts/build_zipapps.py
./dist/hlink-admin studio-prepare \
  --output-dir ./dist/handoff \
  --admin-artifact ./dist/hlink-admin
```

This creates or reuses:

```text
~/.ssh/hlink_to_omarchy
~/.ssh/hlink_to_omarchy.pub
./dist/handoff/studio-executor.pub
```

Only transfer these files to Omarchy:

```text
dist/hlink
dist/hlink-admin
dist/handoff/studio-executor.pub
```

For Tailscale Taildrop, where `OMARCHY_HOST` is the target's Tailscale hostname:

```bash
tailscale file cp \
  dist/hlink \
  dist/hlink-admin \
  dist/handoff/studio-executor.pub \
  OMARCHY_HOST:
```

On Omarchy, retrieve them into an empty directory:

```bash
mkdir -p ~/Downloads/hlink-bootstrap
tailscale file get ~/Downloads/hlink-bootstrap
cd ~/Downloads/hlink-bootstrap
chmod +x hlink hlink-admin
```

### Phase 2 — install the Omarchy endpoint

On Omarchy:

```bash
./hlink-admin omarchy-install \
  --artifact ./hlink \
  --admin-artifact ./hlink-admin \
  --studio-host morpheus.example-tailnet.ts.net \
  --studio-user studio-user \
  --studio-executor-public-key ./studio-executor.pub
```

This installs:

```text
~/.local/bin/hlink
~/.local/bin/hermes-link -> hlink
~/.local/bin/hlink-admin
~/.config/hermes-link/client.json
~/.ssh/hlink_to_morpheus
~/.ssh/hlink_to_morpheus.pub
```

It also adds a marked `restrict` entry for Morpheus's execution key to Omarchy's `~/.ssh/authorized_keys`. Unrelated keys are preserved.

Ensure `~/.local/bin` is on `PATH`, then transfer only this public key back to Morpheus:

```bash
tailscale file cp ~/.ssh/hlink_to_morpheus.pub morpheus:
```

### Phase 3 — authorize Omarchy on Morpheus

Retrieve the public key on the Studio, then run:

```bash
mkdir -p ~/Downloads/hlink-inbox
tailscale file get ~/Downloads/hlink-inbox

./dist/hlink-admin studio-finish \
  --artifact ./dist/hlink-server \
  --executor-host OMARCHY_HOST.example-tailnet.ts.net \
  --executor-user OMARCHY_USER \
  --client-public-key ~/Downloads/hlink-inbox/hlink_to_morpheus.pub \
  --hermes-command ~/.local/bin/hermes \
  --hermes-source-root ~/.hermes/hermes-agent
```

Use the live Omarchy MagicDNS hostname and the output of `whoami`; do not guess either value.

This installs:

```text
~/.local/bin/hlink-server
~/.local/bin/hlink-admin
~/.config/hermes-link/server.json
```

It also adds a marked Studio `authorized_keys` entry with:

```text
restrict,pty,command="/absolute/path/to/hlink-server"
```

That key can launch Hermes Link with a PTY but cannot obtain a Studio shell or create SSH tunnels.

## Use

From an Omarchy project directory:

```bash
hlink
```

Session controls:

```bash
hlink --continue
hlink --continue "session name"
hlink --resume "session-id-or-title"
hlink --cwd /absolute/omarchy/path
hermes-link
```

Hermes Link always adds `--no-restore-cwd`, so a resumed Studio session cannot replace the Omarchy directory supplied by the client.

## Diagnose

On Omarchy:

```bash
hlink --doctor
```

A successful JSON result identifies both sides without exposing key contents:

```json
{
  "status": "ok",
  "studio": {
    "hostname": "Morpheus",
    "profile": "default"
  },
  "executor": {
    "hostname": "omarchy-host",
    "user": "local-user",
    "cwd": "/home/local-user/project",
    "system": "Linux"
  }
}
```

Common failures:

- **Studio host unavailable:** confirm `tailscale status` on both machines.
- **Permission denied from Omarchy to Studio:** rerun Phase 3 with the exact Omarchy client `.pub` file.
- **Permission denied from Studio to Omarchy:** rerun Phase 2 with the exact Studio execution `.pub` file.
- **Working directory missing:** launch inside an existing Omarchy directory or pass `--cwd`.
- **Host-key mismatch:** investigate the machine identity. Hermes Link never disables host-key checking.
- **No TUI/garbled terminal:** launch from an interactive terminal; the client forces a PTY and forwards terminal resize events through OpenSSH.
- **Missing safe SSH no-sync capability:** use a Hermes build that exports `REMOTE_STATE_SYNC_CONTROL_VERSION = 1` and honors `TERMINAL_SYNC_REMOTE_STATE=false`. Hlink intentionally refuses to start on stock builds that would copy state.

## Revoke

On the Studio, block Omarchy from launching Hermes Link immediately:

```bash
hlink-admin revoke --side studio
```

On Omarchy, block Morpheus from executing commands immediately:

```bash
hlink-admin revoke --side omarchy
```

The commands remove only their marked blocks from `authorized_keys`. For full removal, also delete the dedicated `hlink_to_*` key pair, corresponding JSON config, and installed executables after revocation.

## Security

Read [SECURITY.md](SECURITY.md) before enabling the link. The two intentional grants are:

1. The Omarchy key may start the full Morpheus Hermes TUI, but cannot open an ordinary Studio SSH shell.
2. The Studio execution key may run non-PTY commands as the normal Omarchy user because that is how Hermes operates locally.

No passwordless sudo is configured.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests scripts
python3 scripts/build_zipapps.py
./dist/hlink --version
./dist/hlink-server --version
./dist/hlink-admin --help
```

Design and plan:

- `docs/superpowers/specs/2026-09-04-hermes-link-design.md`
- `docs/superpowers/plans/2026-09-04-hermes-link.md`
