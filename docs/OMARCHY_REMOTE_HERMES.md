# Configure Omarchy with a remote Hermes Agent

This guide installs Hermachy 0.5.2 and connects an Omarchy workstation to a Hermes Agent running on another machine. You start `hermachy` in an Omarchy project directory and interact with the native Hermes TUI rendered locally. Native terminal, file, and code tools run on Omarchy; `terminal(target="hermes")` also lets the agent choose the remote Hermes host for commands, CLI tools, files and code. See [connection recovery and execution targets](../README.md#connection-recovery-and-execution-targets) for routing and recovery behavior.

## 1. Understand the boundary

Use these names while following the guide:

| Name | Meaning |
|---|---|
| **Omarchy** | Your local Linux workstation and execution machine |
| **Remote host** | The machine that runs your authoritative Hermes gateway and agent |
| `OMARCHY_HOST` | Omarchy's Tailscale MagicDNS name or tailnet IP |
| `OMARCHY_USER` | Your normal, non-root Omarchy account |
| `REMOTE_HOST` | The remote Hermes host's Tailscale MagicDNS name or tailnet IP |
| `REMOTE_USER` | The account that owns the remote Hermes installation |

The commands still use `studio-*` option names because that is the current Hermachy protocol terminology. The remote host does **not** have to be a Mac Studio, but the current Hermachy admin/server implementation is POSIX-only: use macOS or Linux with Python, OpenSSH, Tailscale, and a compatible Hermes installation. Native Windows remote hosts are not supported in this pre-alpha.

The supported setup uses the remote account's default Hermes profile at `~/.hermes`.
The local TUI connects to a remote gateway; it does not launch a remote renderer.
Configure the default profile explicitly as shown in the compatibility guide,
and do not set a custom `HERMES_HOME` for the SSH server. The Omarchy client
receives access to that profile's agent and enabled non-terminal tools. Use only
a trusted Omarchy account, or isolate Hermes in a separate operating-system account.

Hermachy installs two dedicated Ed25519 key pairs:

```text
Omarchy private key ──────► remote host forced command
remote-host private key ─► Omarchy non-PTY execution
```

Only public keys cross machines. Never copy a private key, Hermes `.env`, `auth.json`, profile directory, skill directory, cache, or session database.

## 2. Prerequisites

### Omarchy

Install and start the required packages:

```bash
sudo pacman -S --needed git openssh python nodejs tailscale
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
- Node.js for the local native TUI
- OpenSSH server and client
- Tailscale online in the same tailnet as the remote host
- an existing absolute project directory, such as `/home/alex/code/project`
- no passwordless sudo is required or added

Desktop control requires an active Hyprland session and its user service bus.
Graphical administration additionally requires `pkexec` and the desktop's polkit
authentication agent. Headless terminal and file operations remain available
without desktop access; diagnostics report these capabilities separately.

### Remote Hermes host

Install Tailscale and OpenSSH using the operating system's normal method. On macOS, enable **System Settings → General → Sharing → Remote Login**. On Linux, enable the distribution's `sshd` service.

Install the [pinned compatible Hermes runtime](../compat/hermes/README.md) as
`REMOTE_USER` before proceeding. That guide fetches a public upstream commit,
applies the included capability patch, installs locked dependencies with Python
3.14 through Hermes PM, and walks through provider setup. An arbitrary stock Hermes installation
does not satisfy this prerequisite.

After following it, Hermes must work locally for `REMOTE_USER`:

```bash
whoami
hostname
"$HOME/.local/share/hermachy/hermes-v0.22.2/venv/bin/hermes" --profile=default --version
"$HOME/.local/share/hermachy/hermes-v0.22.2/venv/bin/hermes" --profile=default doctor
python3 --version
tailscale status
```

The remote host must provide a source/runtime directory containing:

```text
venv/bin/python
venv/bin/hermes
tools/environments/ssh.py
```

The compatibility guide installs this source/runtime directory:

```text
~/.local/share/hermachy/hermes-v0.22.2
```

### Required Hermes safe-executor capability

The remote Hermes build must implement Hermachy safe-executor capability v2. Check it from the Hermes source/runtime directory:

```bash
cd ~/.local/share/hermachy/hermes-v0.22.2
venv/bin/python -c 'from tools.environments.ssh import HERMACHY_CONTROL_VERSION; print(HERMACHY_CONTROL_VERSION)'
```

Required output:

```text
2
```

This capability must enforce the full no-sync, dedicated-identity, process-route contract described in [SECURITY.md](../SECURITY.md), including the protected setting `TERMINAL_SYNC_REMOTE_STATE=false`. A hand-added constant is not sufficient. If the import fails or prints anything other than `2`, stop: `hermachy-server` will reject the runtime, and disabling that check would risk copying remote Hermes state to Omarchy.

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

Choose the MagicDNS names shown by Tailscale. Confirm each machine can reach the other before installing Hermachy:

```bash
# On Omarchy
ping -c 1 REMOTE_HOST

# On the remote host
ping -c 1 OMARCHY_HOST
```

Replace `REMOTE_HOST`, `REMOTE_USER`, `OMARCHY_HOST`, and `OMARCHY_USER` in the
commands with your actual values. Shell variables such as `$HOME` expand normally.

## 4. Verify and record SSH host keys before first Hermachy connection

Hermachy uses `StrictHostKeyChecking=yes`. A previously unseen or changed key is rejected, so verify and record both destination keys before running Hermachy.

On each destination machine, display its Ed25519 host-key fingerprint locally:

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

On Omarchy, retrieve the public key offered by the exact `REMOTE_HOST` name you will configure:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
ssh-keyscan -H -t ed25519 REMOTE_HOST > /tmp/hermachy-remote-hostkey
ssh-keygen -lf /tmp/hermachy-remote-hostkey
```

Compare that fingerprint out-of-band with the fingerprint printed on the remote host. Only after they match:

```bash
cat /tmp/hermachy-remote-hostkey >> ~/.ssh/known_hosts
chmod 600 ~/.ssh/known_hosts
rm /tmp/hermachy-remote-hostkey
```

On the remote host, repeat the process for the exact `OMARCHY_HOST` name:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
ssh-keyscan -H -t ed25519 OMARCHY_HOST > /tmp/hermachy-omarchy-hostkey
ssh-keygen -lf /tmp/hermachy-omarchy-hostkey
```

Compare it with the fingerprint printed locally on Omarchy. Only after they match:

```bash
cat /tmp/hermachy-omarchy-hostkey >> ~/.ssh/known_hosts
chmod 600 ~/.ssh/known_hosts
rm /tmp/hermachy-omarchy-hostkey
```

If the local public host-key file is unavailable, ask the machine administrator for its fingerprint. If `known_hosts` already contains an entry for either exact name, verify that entry rather than blindly appending another one. Never work around a mismatch with `StrictHostKeyChecking=no`.

## 5. Prepare the Hermachy release directory

Choose one of the following methods. Later steps run from a directory containing
`hermachy`, `hermachy-server`, `hermachy-admin`, `hermachy-gateway`, `hermachy-exec`, and `tui/`.

### Build from source

On the remote host, return to the Hermachy checkout used for Step 2. The
compatibility guide's default location is `~/src/hermachy`; substitute your
checkout path if different:

```bash
cd ~/src/hermachy
PYTHONPATH=src python3 -m unittest discover -s tests -q
python3 scripts/build_zipapps.py
cd dist
```

The five executables are standalone Python zipapps requiring Python 3.9+.
The renderer in `tui/` requires Node.js on Omarchy. The builder verifies renderer
and parser provenance. If you build on Omarchy instead, transfer this complete
`dist/` directory to the remote host and enter the transferred directory there.

### Use a release archive

Build `hermachy-0.5.2.tar.gz` with `python3 scripts/build_release.py --check`
from this checkout, then transfer it and its matching `SHA256SUMS` from
`dist/release/` into the same directory on the remote host. This local version
has not been published as a GitHub release. Verify the archive:

```bash
sha256sum -c SHA256SUMS           # Linux
# On macOS, use: shasum -a 256 -c SHA256SUMS
```

After verification succeeds, extract into a new directory and enter it:

```bash
mkdir hermachy-release
tar -xzf hermachy-0.5.2.tar.gz -C hermachy-release
cd hermachy-release
```

The release archive puts the executables at its root; it has no `dist/` directory.
The 0.5.2 archive includes all five executables, `tui/`, the `compat/hermes/`
installer and patch for Step 2, and these docs. Keep those files together while
completing the installation.

## 6. Phase A — prepare the remote host

On the remote host, from the release directory prepared in Step 5:

```bash
./hermachy-admin studio-prepare \
  --output-dir ./handoff \
  --admin-artifact ./hermachy-admin
```

This creates or validates the remote-host-to-Omarchy execution key:

```text
~/.ssh/hermachy_to_omarchy          # private; stays on the remote host
~/.ssh/hermachy_to_omarchy.pub      # public
./handoff/studio-executor.pub
```

Transfer the complete client release and public handoff key to Omarchy, preserving this layout:

```text
hermachy
hermachy-admin
hermachy-gateway
hermachy-exec
tui/entry.mjs
tui/PROVENANCE.json
tui/LICENSE
studio-executor.pub
```

Use an authenticated file transfer. For example, prepare a bootstrap archive on the remote host:

```bash
tar -czf hermachy-bootstrap.tar.gz hermachy hermachy-admin hermachy-gateway hermachy-exec tui \
  -C handoff studio-executor.pub
```

Transfer `hermachy-bootstrap.tar.gz` to Omarchy, then extract it:

```bash
mkdir -p ~/Downloads/hermachy-bootstrap
tar -xzf ~/Downloads/hermachy-bootstrap.tar.gz -C ~/Downloads/hermachy-bootstrap
cd ~/Downloads/hermachy-bootstrap
```

Do not transfer `~/.ssh/hermachy_to_omarchy`; that private key stays on the remote host.

## 7. Phase B — install the Omarchy endpoint

On Omarchy, from the bootstrap directory:

```bash
./hermachy-admin omarchy-install \
  --artifact ./hermachy \
  --admin-artifact ./hermachy-admin \
  --studio-host REMOTE_HOST \
  --studio-user REMOTE_USER \
  --studio-executor-public-key ./studio-executor.pub
```

The command installs:

```text
~/.local/bin/hermachy
~/.local/bin/hermachy-admin
~/.local/bin/hermachy-gateway
~/.local/bin/hermachy-exec
~/.local/share/hermachy/tui/
~/.config/hermachy/client.json
~/.ssh/hermachy_to_morpheus          # client private key; stays on Omarchy
~/.ssh/hermachy_to_morpheus.pub
```

The `hermachy_to_morpheus` filename is a legacy default and works with any remote Hermes host.

It also places the remote execution public key in a marked, restricted block in `~/.ssh/authorized_keys`, with `hermachy-exec` as its forced command. Existing unrelated entries are preserved. The entry point supplies the current workstation desktop session on each command. Re-run this phase when upgrading from versions before 0.4.3.

Ensure the installed commands are on `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"
command -v hermachy
hermachy --version
```

Persist the `PATH` line in your shell profile if required.

Transfer only Omarchy's new client public key back to the remote host using an
authenticated file transfer. One option is Taildrop:

```bash
tailscale file cp ~/.ssh/hermachy_to_morpheus.pub REMOTE_HOST:
```

[Taildrop requires](https://tailscale.com/docs/features/taildrop) Send Files to be
enabled and both devices to belong to the same Tailscale user; tagged nodes are
not supported. If that does not fit your setup, use an existing ordinary SSH/SFTP
login or another trusted transfer to place the public key at
`~/Downloads/hermachy-inbox/hermachy_to_morpheus.pub` on the remote host. Hermachy's
dedicated client key does not grant an ordinary shell or SFTP access.

## 8. Phase C — finish the remote endpoint

On a Linux remote host, retrieve a Taildrop-delivered public key with:

```bash
mkdir -p ~/Downloads/hermachy-inbox
tailscale file get ~/Downloads/hermachy-inbox
```

On macOS, Taildrop places received files in `~/Downloads`. Move the received key
into the same inbox layout:

```bash
mkdir -p ~/Downloads/hermachy-inbox
mv ~/Downloads/hermachy_to_morpheus.pub ~/Downloads/hermachy-inbox/
```

If you used another transfer method, ensure the key is at that inbox path before
continuing; skip the Taildrop retrieval commands.

Resolve the exact Hermes source/runtime path:

```bash
test -x "$HOME/.local/share/hermachy/hermes-v0.22.2/venv/bin/python"
test -x "$HOME/.local/share/hermachy/hermes-v0.22.2/venv/bin/hermes"
```

Then return to the same release directory used in Phase A and install the server endpoint:

```bash
./hermachy-admin studio-finish \
  --artifact ./hermachy-server \
  --executor-host OMARCHY_HOST \
  --executor-user OMARCHY_USER \
  --client-public-key ~/Downloads/hermachy-inbox/hermachy_to_morpheus.pub \
  --hermes-source-root "$HOME/.local/share/hermachy/hermes-v0.22.2"
```

Change `--hermes-source-root` if the checks in Step 2 found a different absolute path. Hermachy derives both capability verification and the TUI launch from that source root's `venv/bin/python`; it does not trust an unrelated `hermes` wrapper on `PATH`.

This installs:

```text
~/.local/bin/hermachy-server
~/.local/bin/hermachy-admin
~/.config/hermachy/server.json
```

It also adds a marked remote-host `authorized_keys` entry equivalent to:

```text
restrict,pty,command="/absolute/path/to/hermachy-server" PUBLIC_KEY
```

That dedicated key can start Hermachy with a PTY but cannot obtain an ordinary shell or create tunnels on the remote host.

## 9. Run diagnostics

On Omarchy, enter a real local project directory and run:

```bash
cd /absolute/path/to/project
hermachy --doctor
```

A successful report has `"status": "ok"` and identifies:

- the remote host under `studio`;
- the authoritative Hermes profile and version;
- Omarchy under `executor`;
- the expected Omarchy username, Linux system, and current project directory;
- desktop, Wayland, user service bus, execution entry point, and administration
  availability under `executor.access`, with any limitations under `warnings`.

Example shape:

```json
{
  "status": "ok",
  "version": "0.5.2",
  "studio": {
    "hostname": "remote-hermes-host",
    "profile": "default"
  },
  "executor": {
    "hostname": "omarchy-workstation",
    "user": "alex",
    "cwd": "/home/alex/code/project",
    "system": "Linux",
    "access": {
      "version": "1",
      "ssh_entrypoint": true,
      "desktop": true,
      "wayland": true,
      "user_bus": true,
      "administration": {
        "available": true,
        "authentication": "desktop"
      }
    }
  },
  "warnings": []
}
```

Do not continue if the host, user, directory, or operating system is wrong.

`"status": "ok"` confirms the transport and execution identity. A desktop warning
still permits headless work. If `ssh_entrypoint` is missing or false, repeat
Phase B with the 0.5.2 artifacts. Administration availability checks the
authentication path; each privileged command still requires authorization
through the workstation's desktop. See [desktop and system access](../README.md#desktop-and-system-access).

## 10. Use Hermachy

Launch from the Omarchy project you want Hermes to operate on:

```bash
cd ~/code/my-project
hermachy
```

The screen is the native Hermes TUI rendered locally. The gateway, credentials,
and authoritative session/profile storage remain on the remote host. Native
file and code tools use Omarchy. Terminal commands default to Omarchy; the agent
can explicitly choose `target="hermes"` for a command on the remote host.

Session options:

```bash
hermachy                         # new session
hermachy --continue              # latest session
hermachy --continue "name"       # named continuation
hermachy --resume "id-or-title"  # specific remote session
hermachy --cwd /home/alex/code/my-project
```

## 11. Verify execution location

Inside the Hermachy TUI, ask Hermes:

```text
Use the terminal with target="omarchy" to run: hostname; whoami; pwd; uname -s
Then create .hermachy-smoke.txt in the current project with the text hermachy-ok,
read it back with the file tool, and verify it with the code-execution tool.
Do not use sudo and do not touch files outside this project.
```

In a second local Omarchy terminal, verify the result:

```bash
cd ~/code/my-project
cat .hermachy-smoke.txt
rm .hermachy-smoke.txt
```

Expected evidence:

- `hostname` is Omarchy, not the remote host;
- `whoami` is `OMARCHY_USER`;
- `pwd` is the Omarchy project directory;
- `uname -s` is `Linux`;
- terminal, file, and code-execution tools all see the same test file;
- the remote host's Hermes profile state was not copied into Omarchy.

If Omarchy already runs its own Hermes installation, do not infer Hermachy copying from the pre-existing `~/.hermes` directory. Compare a before/after inventory in a disposable test account or inspect the safe-executor implementation and SSH logs.

## 12. Troubleshooting

### Safe-executor capability is missing

Symptom:

```text
installed Hermes must support Hermachy safe-executor capability v2
```

Follow the [compatible runtime installation steps](../compat/hermes/README.md),
then rerun `studio-finish` with that runtime's `--hermes-source-root`. If you
already installed it, run the guide's source verification and import probe to
check for an accidental Hermes update or incorrect virtualenv. Do not edit
Hermachy to skip the check and do not use stock SSH state synchronization.

### Omarchy cannot reach the remote host

On both machines:

```bash
tailscale status
```

Confirm both are online in the same tailnet, then test `ping` and ordinary SSH using the expected Tailscale hostname. Hermachy ignores `~/.ssh/config`, so aliases and implicit usernames in that file do not apply.

### Permission denied from Omarchy to the remote host

Repeat Phase C with the exact `~/.ssh/hermachy_to_morpheus.pub` generated on Omarchy. Do not substitute another private key or enable password fallback.

### Permission denied from the remote host to Omarchy

Repeat Phase B with the exact `studio-executor.pub` generated in Phase A.

### Working directory does not exist

Create the directory on Omarchy or pass a valid absolute path:

```bash
hermachy --cwd /home/alex/code/project
```

### Host-key mismatch

Stop and verify whether the destination was rebuilt or compromised. Compare fingerprints. Do not delete the known-host entry until the new identity has been independently confirmed.

### Garbled or missing TUI

Run `hermachy` from an interactive local terminal with Node.js installed. The native TUI needs a local terminal; the gateway uses SSH JSON-RPC without a remote PTY. Install `hermachy-gateway` and the matching `tui/` assets alongside the client release.

## 13. Revoke access

To block future Omarchy authentication to the remote Hermes host, run on the remote host:

```bash
~/.local/bin/hermachy-admin revoke --side studio
```

To block future remote-host authentication to Omarchy, run on Omarchy:

```bash
~/.local/bin/hermachy-admin revoke --side omarchy
```

The commands remove only Hermachy's marked `authorized_keys` blocks. They do **not** terminate already-authenticated TUI, Hermes, SSH, or persistent control sessions.

After revoking the relevant block:

1. Exit every active Hermachy TUI using this link.
2. On both machines, identify the exact Hermachy-started Hermes process and its associated SSH or control-master processes.
3. Terminate those exact processes without killing unrelated Hermes or SSH sessions.
4. Verify no process or connection associated with the link remains.

Only then treat access as stopped. For permanent removal, delete the corresponding dedicated key pair, JSON configuration, and installed Hermachy executables.

## 14. Upgrade

Upgrade both endpoints and the pinned Hermes runtime as one set:

1. Close active Hermachy clients so their gateways stop and reopened sessions
   load the new runtime.
2. Build this 0.5.2 source checkout or verify and extract its archive using Step 5.
   Keep all five executables and their matching `tui/` assets together.
3. Prepare and verify the matching Hermes runtime in a new directory using the
   [pinned runtime upgrade procedure](../compat/hermes/README.md#validation-and-upgrades).
   Keep the previous runtime directory for rollback.
4. Transfer the client bundle and existing public handoff key as in Phase A,
   then re-run Phase B on Omarchy. This installs `hermachy-exec` and updates the
   execution key's forced command; copying only the old executables is insufficient.
5. Re-run Phase C on the remote host using the new server artifact and verified
   Hermes source root. Managed files and marked key blocks update idempotently.
6. Run `hermachy --doctor` from an Omarchy project directory, check the identities
   and access warnings, then open a new session or resume a saved conversation.

Treat Hermachy, its safe-executor Hermes version, and SSH policy as one compatibility set. Do not unattended-auto-update one side of this security boundary.

The optional `hermachy-app` desktop interface was removed after 0.4.0. Launch
the native terminal interface with `hermachy` after upgrading.

## Security reference

Before granting access, read [SECURITY.md](../SECURITY.md). Anyone holding the Omarchy client private key can interact with the configured remote Hermes profile. Anyone holding the remote execution private key can run non-interactive commands with `OMARCHY_USER` permissions. Protect both accounts and keys accordingly.
