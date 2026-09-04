# Configure Omarchy with a remote Hermes Agent

This guide connects an Omarchy workstation to a Hermes Agent running on another machine. You start `hlink` in an Omarchy project directory, interact with the real remote Hermes TUI, and have Hermes execute terminal, file, and code operations on Omarchy.

## 1. Understand the boundary

Use these names while following the guide:

| Name | Meaning |
|---|---|
| **Omarchy** | Your local Linux workstation and execution machine |
| **Remote host** | The machine that runs your authoritative Hermes Agent and TUI |
| `OMARCHY_HOST` | Omarchy's Tailscale MagicDNS name or tailnet IP |
| `OMARCHY_USER` | Your normal, non-root Omarchy account |
| `REMOTE_HOST` | The remote Hermes host's Tailscale MagicDNS name or tailnet IP |
| `REMOTE_USER` | The account that owns the remote Hermes installation |

The commands still use `studio-*` option names because that is the current Hlink protocol terminology. The remote host does **not** have to be a Mac Studio, but the current Hlink admin/server implementation is POSIX-only: use macOS or Linux with Python, OpenSSH, Tailscale, and a compatible Hermes installation. Native Windows remote hosts are not supported in this pre-alpha.

The current pre-alpha launches `hermes --profile=default`. The Omarchy client therefore receives access to that profile's full interactive agent experience and enabled non-terminal tools. Use only a trusted Omarchy account, or isolate the remote Hermes host at the operating-system account boundary.

Hlink installs two dedicated Ed25519 key pairs:

```text
Omarchy private key ──────► remote host forced command
remote-host private key ─► Omarchy non-PTY execution
```

Only public keys cross machines. Never copy a private key, Hermes `.env`, `auth.json`, profile directory, skill directory, cache, or session database.

## 2. Prerequisites

### Omarchy

Install and start the required packages:

```bash
sudo pacman -S --needed git openssh python tailscale
sudo systemctl enable --now sshd tailscaled
sudo tailscale up
```

Complete the Tailscale login when prompted.

Check the local values that you will use later:

```bash
whoami
hostname
python3 --version
tailscale status
```

Requirements:

- Python 3.9 or newer
- OpenSSH server and client
- Tailscale online in the same tailnet as the remote host
- an existing absolute project directory, such as `/home/alex/code/project`
- no passwordless sudo is required or added

### Remote Hermes host

Install Tailscale and OpenSSH using the operating system's normal method. On macOS, enable **System Settings → General → Sharing → Remote Login**. On Linux, enable the distribution's `sshd` service.

Hermes must already work locally for `REMOTE_USER`:

```bash
whoami
hostname
command -v hermes
hermes --version
hermes doctor
python3 --version
tailscale status
```

The remote host must provide a source/runtime directory containing:

```text
venv/bin/python
venv/bin/hermes
tools/environments/ssh.py
```

A common source installation is:

```text
~/.hermes/hermes-agent
```

### Required Hermes safe-executor capability

The remote Hermes build must implement Hlink safe-executor capability v1. Check it from the Hermes source/runtime directory:

```bash
cd ~/.hermes/hermes-agent
venv/bin/python -c 'from tools.environments.ssh import HERMES_LINK_CONTROL_VERSION; print(HERMES_LINK_CONTROL_VERSION)'
```

Required output:

```text
1
```

This capability must enforce the full no-sync, dedicated-identity, process-route contract described in [SECURITY.md](../SECURITY.md), including the protected setting `TERMINAL_SYNC_REMOTE_STATE=false`. A hand-added constant is not sufficient. If the import fails or prints anything other than `1`, stop: `hlink-server` will reject the runtime, and disabling that check would risk copying remote Hermes state to Omarchy.

## 3. Record and verify both Tailscale identities

Run on Omarchy:

```bash
printf 'OMARCHY_USER=%s\n' "$(whoami)"
tailscale status
```

Run on the remote host:

```bash
printf 'REMOTE_USER=%s\n' "$(whoami)"
tailscale status
```

Choose the MagicDNS names shown by Tailscale. Confirm each machine can reach the other before installing Hlink:

```bash
# On Omarchy
ping -c 1 REMOTE_HOST

# On the remote host
ping -c 1 OMARCHY_HOST
```

Replace every uppercase placeholder in the rest of this guide with the real value. Do not include angle brackets in commands.

## 4. Verify and record SSH host keys before first Hlink connection

Hlink uses `StrictHostKeyChecking=accept-new`: a previously recorded key is enforced, a previously unseen key is stored, and later key changes are rejected. Because an automatically accepted first key is not independently authenticated, verify and record both keys before running Hlink.

On each destination machine, display its Ed25519 host-key fingerprint locally:

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

On Omarchy, retrieve the public key offered by the exact `REMOTE_HOST` name you will configure:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
ssh-keyscan -H -t ed25519 REMOTE_HOST > /tmp/hlink-remote-hostkey
ssh-keygen -lf /tmp/hlink-remote-hostkey
```

Compare that fingerprint out-of-band with the fingerprint printed on the remote host. Only after they match:

```bash
cat /tmp/hlink-remote-hostkey >> ~/.ssh/known_hosts
chmod 600 ~/.ssh/known_hosts
rm /tmp/hlink-remote-hostkey
```

On the remote host, repeat the process for the exact `OMARCHY_HOST` name:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
ssh-keyscan -H -t ed25519 OMARCHY_HOST > /tmp/hlink-omarchy-hostkey
ssh-keygen -lf /tmp/hlink-omarchy-hostkey
```

Compare it with the fingerprint printed locally on Omarchy. Only after they match:

```bash
cat /tmp/hlink-omarchy-hostkey >> ~/.ssh/known_hosts
chmod 600 ~/.ssh/known_hosts
rm /tmp/hlink-omarchy-hostkey
```

If the local public host-key file is unavailable, ask the machine administrator for its fingerprint. If `known_hosts` already contains an entry for either exact name, verify that entry rather than blindly appending another one. Never work around a mismatch with `StrictHostKeyChecking=no`.

## 5. Build Hlink

On either trusted machine:

```bash
git clone https://github.com/kzndk/hlink.git
cd hlink
PYTHONPATH=src python3 -m unittest discover -s tests -q
python3 scripts/build_zipapps.py
```

This creates:

```text
dist/hlink
dist/hlink-server
dist/hlink-admin
```

The files are standalone Python zipapps. Their runtime needs only Python 3.9+.

For the following steps, keep this repository checkout on the remote host. If you built on Omarchy, transfer the repository or the three `dist/` artifacts to the remote host without transferring any private key.

## 6. Phase A — prepare the remote host

On the remote host, from the Hlink repository:

```bash
./dist/hlink-admin studio-prepare \
  --output-dir ./dist/handoff \
  --admin-artifact ./dist/hlink-admin
```

This creates or validates the remote-host-to-Omarchy execution key:

```text
~/.ssh/hlink_to_omarchy          # private; stays on the remote host
~/.ssh/hlink_to_omarchy.pub      # public
./dist/handoff/studio-executor.pub
```

Transfer **only** these files to Omarchy:

```text
dist/hlink
dist/hlink-admin
dist/handoff/studio-executor.pub
```

For example, with Tailscale Taildrop (an optional Tailscale feature that must be enabled and supports transfers between your own untagged devices):

```bash
# On the remote host
tailscale file cp \
  dist/hlink \
  dist/hlink-admin \
  dist/handoff/studio-executor.pub \
  OMARCHY_HOST:
```

Then on Omarchy:

```bash
mkdir -p ~/Downloads/hlink-bootstrap
tailscale file get ~/Downloads/hlink-bootstrap
cd ~/Downloads/hlink-bootstrap
chmod +x hlink hlink-admin
```

If Taildrop is unavailable, use another authenticated transfer method. Do not transfer `~/.ssh/hlink_to_omarchy`.

## 7. Phase B — install the Omarchy endpoint

On Omarchy, from the bootstrap directory:

```bash
./hlink-admin omarchy-install \
  --artifact ./hlink \
  --admin-artifact ./hlink-admin \
  --studio-host REMOTE_HOST \
  --studio-user REMOTE_USER \
  --studio-executor-public-key ./studio-executor.pub
```

The command installs:

```text
~/.local/bin/hlink
~/.local/bin/hermes-link -> hlink
~/.local/bin/hlink-admin
~/.config/hermes-link/client.json
~/.ssh/hlink_to_morpheus          # client private key; stays on Omarchy
~/.ssh/hlink_to_morpheus.pub
```

The `hlink_to_morpheus` filename is a legacy default and works with any remote Hermes host.

It also places the remote execution public key in a marked, restricted block in `~/.ssh/authorized_keys`. Existing unrelated entries are preserved.

Ensure the installed commands are on `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"
command -v hlink
hlink --version
```

Persist the `PATH` line in your shell profile if required.

Transfer only Omarchy's new client public key back to the remote host:

```bash
tailscale file cp ~/.ssh/hlink_to_morpheus.pub REMOTE_HOST:
```

## 8. Phase C — finish the remote endpoint

On the remote host, retrieve the public key:

```bash
mkdir -p ~/Downloads/hlink-inbox
tailscale file get ~/Downloads/hlink-inbox
```

Resolve the exact Hermes source/runtime path:

```bash
test -x "$HOME/.hermes/hermes-agent/venv/bin/python"
test -x "$HOME/.hermes/hermes-agent/venv/bin/hermes"
```

Then, from the Hlink repository, install the server endpoint:

```bash
./dist/hlink-admin studio-finish \
  --artifact ./dist/hlink-server \
  --executor-host OMARCHY_HOST \
  --executor-user OMARCHY_USER \
  --client-public-key ~/Downloads/hlink-inbox/hlink_to_morpheus.pub \
  --hermes-source-root "$HOME/.hermes/hermes-agent"
```

Change `--hermes-source-root` if the checks in Step 2 found a different absolute path. Hlink derives both capability verification and the TUI launch from that source root's `venv/bin/python`; it does not trust an unrelated `hermes` wrapper on `PATH`.

This installs:

```text
~/.local/bin/hlink-server
~/.local/bin/hlink-admin
~/.config/hermes-link/server.json
```

It also adds a marked remote-host `authorized_keys` entry equivalent to:

```text
restrict,pty,command="/absolute/path/to/hlink-server" PUBLIC_KEY
```

That dedicated key can start Hlink with a PTY but cannot obtain an ordinary shell or create tunnels on the remote host.

## 9. Run diagnostics

On Omarchy, enter a real local project directory and run:

```bash
cd /absolute/path/to/project
hlink --doctor
```

A successful report has `"status": "ok"` and identifies:

- the remote host under `studio`;
- the authoritative Hermes profile and version;
- Omarchy under `executor`;
- the expected Omarchy username, Linux system, and current project directory.

Example shape:

```json
{
  "status": "ok",
  "studio": {
    "hostname": "remote-hermes-host",
    "profile": "default"
  },
  "executor": {
    "hostname": "omarchy-workstation",
    "user": "alex",
    "cwd": "/home/alex/code/project",
    "system": "Linux"
  }
}
```

Do not continue if the host, user, directory, or operating system is wrong.

## 10. Use Hlink

Launch from the Omarchy project you want Hermes to operate on:

```bash
cd ~/code/my-project
hlink
```

The screen is the real remote Hermes TUI. Its models, memory, skills, sessions, and credentials remain on the remote host. Terminal, file, search, patch, build, test, and code-execution tools use Omarchy.

Session options:

```bash
hlink                         # new session
hlink --continue              # latest session
hlink --continue "name"       # named continuation
hlink --resume "id-or-title"  # specific remote session
hlink --cwd /home/alex/code/my-project
hermes-link                   # alias
```

## 11. Verify execution location

Inside the Hlink TUI, ask Hermes:

```text
Use the terminal to run: hostname; whoami; pwd; uname -s
Then create .hlink-smoke.txt in the current project with the text hlink-ok,
read it back with the file tool, and verify it with the code-execution tool.
Do not use sudo and do not touch files outside this project.
```

In a second local Omarchy terminal, verify the result:

```bash
cd ~/code/my-project
cat .hlink-smoke.txt
rm .hlink-smoke.txt
```

Expected evidence:

- `hostname` is Omarchy, not the remote host;
- `whoami` is `OMARCHY_USER`;
- `pwd` is the Omarchy project directory;
- `uname -s` is `Linux`;
- terminal, file, and code-execution tools all see the same test file;
- the remote host's Hermes profile state was not copied into Omarchy.

If Omarchy already runs its own Hermes installation, do not infer Hlink copying from the pre-existing `~/.hermes` directory. Compare a before/after inventory in a disposable test account or inspect the safe-executor implementation and SSH logs.

## 12. Troubleshooting

### Safe-executor capability is missing

Symptom:

```text
installed Hermes lacks Hermes Link safe-executor capability v1
```

Use a Hermes build that implements the complete capability contract. Do not edit Hlink to skip the check and do not use stock SSH state synchronization.

### Omarchy cannot reach the remote host

On both machines:

```bash
tailscale status
```

Confirm both are online in the same tailnet, then test `ping` and ordinary SSH using the expected Tailscale hostname. Hlink ignores `~/.ssh/config`, so aliases and implicit usernames in that file do not apply.

### Permission denied from Omarchy to the remote host

Repeat Phase C with the exact `~/.ssh/hlink_to_morpheus.pub` generated on Omarchy. Do not substitute another private key or enable password fallback.

### Permission denied from the remote host to Omarchy

Repeat Phase B with the exact `studio-executor.pub` generated in Phase A.

### Working directory does not exist

Create the directory on Omarchy or pass a valid absolute path:

```bash
hlink --cwd /home/alex/code/project
```

### Host-key mismatch

Stop and verify whether the destination was rebuilt or compromised. Compare fingerprints. Do not delete the known-host entry until the new identity has been independently confirmed.

### Garbled or missing TUI

Run `hlink` from an interactive terminal. Hlink requests an SSH PTY and the real Hermes TUI requires one.

## 13. Revoke access

To stop Omarchy from launching the remote Hermes Agent immediately, run on the remote host:

```bash
~/.local/bin/hlink-admin revoke --side studio
```

To stop the remote Hermes host from executing on Omarchy immediately, run on Omarchy:

```bash
~/.local/bin/hlink-admin revoke --side omarchy
```

The commands remove only Hlink's marked `authorized_keys` blocks. For permanent removal, revoke first and then delete the corresponding dedicated key pair, JSON configuration, and installed Hlink executables.

## 14. Upgrade

Build the new Hlink version from a verified source revision. Re-run the relevant install phase with the new artifacts; managed files and marked blocks are updated idempotently. Run `hlink --doctor` again before opening a session.

Treat Hlink, its safe-executor Hermes version, and SSH policy as one compatibility set. Do not unattended-auto-update one side of this security boundary.

## Security reference

Before granting access, read [SECURITY.md](../SECURITY.md). Anyone holding the Omarchy client private key can interact with the configured remote Hermes profile. Anyone holding the remote execution private key can run non-interactive commands with `OMARCHY_USER` permissions. Protect both accounts and keys accordingly.
