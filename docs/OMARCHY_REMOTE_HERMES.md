# Configure Omarchy with a remote Hermes Agent

This guide installs Hermachy 0.6.0 and connects an Omarchy workstation to a Hermes Agent running on another machine. You start `hermachy` in an Omarchy project directory and interact with the native Hermes TUI rendered locally. Hermachy chats live in the remote host's default Hermes profile, alongside Hermes Desktop and the profile's other surfaces. Their terminal, process, file, and code tools run on Omarchy; the `hermes_host` tool also lets the agent choose the remote Hermes host for commands, CLI tools, files and code. See [connection recovery and execution targets](../README.md#connection-recovery-and-execution-targets) for routing and recovery behavior.

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

The commands still use `studio-*` option names because that is the current Hermachy protocol terminology. The remote host does **not** have to be a Mac Studio, but the current Hermachy admin/server implementation is POSIX-only: use macOS or Linux with Python, OpenSSH, Tailscale, and Hermes Agent v0.23.1 or later. Native Windows remote hosts are not supported in this pre-alpha.

Hermachy uses the remote account's **default** Hermes profile, normally at `~/.hermes`.
The local TUI attaches to that profile's running gateway, the same normal
launchd/systemd `hermes gateway` service that Hermes Desktop and messaging use.
Hermachy does not start a gateway of its own or launch a remote renderer. Its chats
share the profile's chats, memory, skills, and session list with the other
surfaces. A Hermachy plugin in that profile routes only the chats that Hermachy
created or resumed to Omarchy, through its own `hermachy_ssh` terminal backend;
other chats are untouched. The Omarchy client receives access to that profile's
agent, its chats, and the gateway methods that Hermachy's bridge allows. Use only
a trusted Omarchy account, or isolate Hermes in a separate operating-system
account.

Hermachy installs two dedicated Ed25519 key pairs:

```text
Omarchy private key ──────► remote host forced command
remote-host private key ─► Omarchy non-PTY execution
```

The remote-host private key is used by the Hermachy plugin inside the shared
gateway process. Only public keys cross machines. Never copy a private key, Hermes `.env`, `auth.json`, profile directory, skill directory, cache, or session database.

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

Install stock Hermes Agent **v0.23.1 or later** as `REMOTE_USER` using the
[upstream instructions](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.1),
and configure its provider in the default profile. No patch or separate Hermachy
runtime is needed. Run the default profile's gateway as its normal background
service; if it is not installed yet, use `hermes gateway install` and
`hermes gateway start`.

Hermes must then work locally for `REMOTE_USER`:

```bash
whoami
hostname
command -v hermes
hermes --version
hermes gateway status
python3 --version
tailscale status
```

### Required Hermes version and gateway

Hermachy 0.6.0 requires Hermes **v0.23.1 or later** on the remote host. The
Hermachy plugin registers a terminal-environment provider, a hook that Hermes
v0.23.1 introduced; v0.23.0 cannot load it. The bridge runs the host's own
`hermes --profile default gateway attach --json` to find the running gateway and
obtain a single-use attach ticket. It never starts a gateway under sshd. If the
gateway is not running, Hermachy fails with Hermes's own message.

`hermachy-server` runs that `hermes` launcher with a minimal environment rather
than sshd's or the client's. Phase C records the launcher's absolute path; use the
same installation that runs the default profile's gateway.

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
`hermachy`, `hermachy-server`, `hermachy-admin`, `hermachy-exec`, `tui/`, and
`plugin/hermachy/`.

### Build from source

On the remote host, use a Hermachy 0.6.0 source checkout. These steps assume
`~/src/hermachy`; substitute your checkout path if different:

```bash
cd ~/src/hermachy
PYTHONPATH=src python3 -m unittest discover -s tests -q
python3 scripts/build_zipapps.py
cd dist
```

The four executables are standalone Python zipapps requiring Python 3.9+.
The renderer in `tui/` requires Node.js on Omarchy. The builder verifies renderer
and parser provenance. If you build on Omarchy instead, transfer this complete
`dist/` directory to the remote host and enter the transferred directory there.

### Use a release archive

Build `hermachy-0.6.0.tar.gz` with `python3 scripts/build_release.py --check`
from this checkout, then transfer it and its matching `SHA256SUMS` from
`dist/release/` into the same directory on the remote host. Verify the archive:

```bash
sha256sum -c SHA256SUMS           # Linux
# On macOS, use: shasum -a 256 -c SHA256SUMS
```

After verification succeeds, extract into a new directory and enter it:

```bash
mkdir hermachy-release
tar -xzf hermachy-0.6.0.tar.gz -C hermachy-release
cd hermachy-release
```

The release archive puts the executables at its root; it has no `dist/` directory.
The 0.6.0 archive includes all four executables, `tui/`, `plugin/hermachy/`,
`release-sha256.json`, and these docs. Keep those files together while completing
the installation.

## 6. Phase A — prepare the remote host

On the remote host, from the release directory prepared in Step 5:

```bash
./hermachy-admin studio-prepare \
  --output-dir ./handoff \
  --admin-artifact ./hermachy-admin
```

This installs `~/.local/bin/hermachy-admin` and creates or validates the remote-host-to-Omarchy execution key:

```text
~/.ssh/hermachy_to_omarchy          # private; stays on the remote host
~/.ssh/hermachy_to_omarchy.pub      # public
./handoff/studio-executor.pub
```

Transfer the complete client release and public handoff key to Omarchy, preserving this layout:

```text
hermachy
hermachy-admin
hermachy-exec
tui/entry.mjs
tui/PROVENANCE.json
tui/LICENSE
tui/activity-emojis.patch
tui/omarchy-theme.patch
tui/scripts/clipboard_image.py
studio-executor.pub
```

`hermachy-server` and `plugin/hermachy/` stay on the remote host.

Use an authenticated file transfer. For example, prepare a bootstrap archive on the remote host:

```bash
tar -czf hermachy-bootstrap.tar.gz hermachy hermachy-admin hermachy-exec tui \
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
~/.local/bin/hermachy-exec
~/.local/share/hermachy/tui/
~/.config/hermachy/client.json
~/.ssh/hermachy_to_morpheus          # client private key; stays on Omarchy
~/.ssh/hermachy_to_morpheus.pub
```

The `hermachy_to_morpheus` filename is a legacy default and works with any remote Hermes host. The installer also removes a stale `~/.local/bin/hermachy-gateway` left by 0.5.x; the client now contains its own loopback relay.

It also places the remote execution public key in a marked, restricted block in `~/.ssh/authorized_keys`, with `hermachy-exec` as its forced command. Existing unrelated entries are preserved. The entry point supplies the current workstation desktop session on each command. Re-run this phase when upgrading.

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

Confirm the Hermes launcher and the default profile's home:

```bash
command -v hermes
hermes --version
test -d "$HOME/.hermes"
```

Then return to the same release directory used in Phase A and install the server endpoint and plugin:

```bash
./hermachy-admin studio-finish \
  --artifact ./hermachy-server \
  --executor-host OMARCHY_HOST \
  --executor-user OMARCHY_USER \
  --client-public-key ~/Downloads/hermachy-inbox/hermachy_to_morpheus.pub \
  --hermes-command "$(command -v hermes)"
```

`--hermes-command` defaults to `hermes` on `PATH` and is recorded as an absolute
path. Pass `--hermes-home` if the default profile's home is not `~/.hermes`. Other
options: `--plugin-dir` (default: `plugin/hermachy` beside `--artifact`),
`--executor-port` (default 22), `--executor-key` (default
`~/.ssh/hermachy_to_omarchy`), and `--no-enable` to install the plugin without
enabling it.

This installs:

```text
~/.local/bin/hermachy-server
~/.config/hermachy/server.json
~/.hermes/plugins/hermachy/          # plugin.yaml and __init__.py
~/.hermes/hermachy/                  # private binding directories
```

With `--hermes-home`, the last two are created under that directory instead.

`server.json` is mode `0600` and contains `executor_host`, `executor_identity_file`,
`executor_port`, `executor_user`, `hermes_command`, and `hermes_home`. Unless
`--no-enable` is given, the command also enables the plugin in the default profile:

```bash
hermes --profile default plugins enable hermachy --no-allow-tool-override
```

`plugins enable` adds the plugin's toolset to every platform. Hermachy sessions
use the `cli` platform only, so the command then removes the `hermachy` toolset
from the others (for example `hermes --profile default tools disable hermachy
--platform slack`): messaging surfaces never see the `hermes_host` tool.

It also adds a marked remote-host `authorized_keys` entry equivalent to:

```text
restrict,command="/absolute/path/to/hermachy-server" PUBLIC_KEY
```

That dedicated key can run only the Hermachy server. It cannot allocate a PTY, obtain an ordinary shell, or create tunnels on the remote host.

Finally, restart the default profile's gateway once so it loads the plugin:

```bash
hermes gateway restart
```

This restarts the gateway that also serves the profile's other surfaces; choose a
suitable moment. Until it restarts, the running gateway has not necessarily loaded
the plugin, and without the plugin a Hermachy chat's tools would run on the host.
`hermachy --doctor` checks that the plugin is installed and enabled but cannot see
which plugins the running gateway has loaded. Complete Step 11 before relying on
the route.

## 9. Run diagnostics

On Omarchy, enter a real local project directory and run:

```bash
cd /absolute/path/to/project
hermachy --doctor
```

A successful report has `"status": "ok"` and identifies:

- the remote host under `studio`, with its user, the `default` profile, and the
  output of `hermes --version`;
- `"gateway": "attachable"`, after a real attach and `ping` to the default
  profile's gateway;
- the installed plugin version, which must equal the client and server version;
- the `shared-gateway-v1` transport;
- Omarchy under `executor`;
- the expected Omarchy username, Linux system, and current project directory;
- desktop, Wayland, user service bus, execution entry point, and administration
  availability under `executor.access`, with any limitations under `warnings`;
- the local renderer bundle under `client`.

Example shape:

```json
{
  "status": "ok",
  "product": "Hermachy",
  "version": "0.6.0",
  "transport": "shared-gateway-v1",
  "execution_targets": ["omarchy", "hermes"],
  "studio": {
    "hostname": "remote-hermes-host",
    "user": "hermes-owner",
    "profile": "default",
    "hermes": "<output of hermes --version>",
    "gateway": "attachable",
    "plugin": "0.6.0"
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
  "warnings": [],
  "client": {
    "hostname": "omarchy-workstation",
    "system": "linux",
    "renderer": "local native Hermes TUI",
    "bundle": "/home/alex/.local/share/hermachy/tui/entry.mjs"
  }
}
```

Do not continue if the host, user, directory, or operating system is wrong.

`"status": "ok"` confirms the transport, the gateway, the installed plugin, and
the execution identity. A desktop warning still permits headless work. If
`ssh_entrypoint` is missing or false, repeat Phase B with the 0.6.0 artifacts.
Administration availability checks the authentication path; each privileged
command still requires authorization through the workstation's desktop. See
[desktop and system access](../README.md#desktop-and-system-access).

## 10. Use Hermachy

Launch from the Omarchy project you want Hermes to operate on:

```bash
cd ~/code/my-project
hermachy
```

The screen is the native Hermes TUI rendered locally. The gateway, credentials,
and authoritative session/profile storage remain in the remote host's default
profile. In a Hermachy chat, the terminal, process, file, and code tools use
Omarchy, starting in the launch directory; the agent can explicitly choose
`hermes_host` for a command on the remote host.

Session options:

```bash
hermachy                         # new chat bound to this directory
hermachy --continue              # latest Hermachy chat for this directory
hermachy --continue "name"       # chat by title
hermachy --resume "id-or-title"  # specific chat of the default profile
hermachy --cwd /home/alex/code/my-project
```

`hermachy --continue` falls back to the profile's most recent chat when no
Hermachy chat is bound to this directory. Resuming any chat of the profile in
Hermachy, including one started in Hermes Desktop, binds it to this Omarchy
directory. A binding belongs to the chat: if you later continue it in Desktop, its
tools still run on Omarchy.

Hermes launch options such as `--model`, `--provider`, `--skills`, and
`--reasoning` apply to new chats. `--safe-mode` and `--ignore-user-config` are
refused because those sessions skip plugins, and with them the workstation route.
Inline image previews, `@path` completion, and host file attachments are
unavailable in Hermachy; images from Omarchy are uploaded as bytes.

## 11. Verify execution location

Inside the Hermachy TUI, ask Hermes:

```text
Use the terminal to run: hostname; whoami; pwd; uname -s
Then create .hermachy-smoke.txt in the current project with the text hermachy-ok,
read it back with the file tool, and verify it with the code-execution tool.
Finally, use hermes_host to run: hostname
Do not use sudo and do not touch files outside this project.
```

In a second local Omarchy terminal, verify the result:

```bash
cd ~/code/my-project
cat .hermachy-smoke.txt
rm .hermachy-smoke.txt
```

Expected evidence:

- `hostname` from the terminal is Omarchy, not the remote host;
- `whoami` is `OMARCHY_USER`;
- `pwd` is the Omarchy project directory;
- `uname -s` is `Linux`;
- terminal, file, and code-execution tools all see the same test file;
- `hermes_host` reports the remote host's hostname;
- the remote host's Hermes profile state was not copied into Omarchy.

The bridge refuses to attach when the running gateway has not loaded the
Hermachy plugin, so a misrouted session should not occur. If the terminal
nevertheless reports the remote host, stop and report it as a bug: close the
TUI, remove any `.hermachy-smoke.txt` created under
`~/.hermes/hermachy/workspaces/` on the remote host, and run `hermachy --doctor`.

If Omarchy already runs its own Hermes installation, do not infer Hermachy copying from the pre-existing `~/.hermes` directory. Compare a before/after inventory in a disposable test account or inspect the plugin implementation and SSH logs.

## 12. Troubleshooting

### Gateway is not attachable

Symptom, in the `error` field of the diagnostic report:

```text
the default profile's Hermes gateway is not attachable: …
```

Hermachy never starts a gateway under sshd. On the remote host, check
`hermes gateway status` and start the default profile's gateway service. Confirm
that `hermes_command` in `~/.config/hermachy/server.json` is the installation that
runs the gateway, and rerun `studio-finish` with `--hermes-command` if it is not.

### Hermachy plugin is missing, stale, or not enabled

Symptoms, in the `error` field of the diagnostic report:

```text
Hermachy plugin missing is installed; expected 0.6.0; re-run hermachy-admin studio-finish
the Hermachy plugin is not listed in plugins.enabled of the default profile
```

Rerun Phase C with the 0.6.0 release directory. Then run `hermes gateway restart`
and repeat Step 11. The bridge refuses every attach until the running gateway
reports the plugin as loaded, with the same version and no load error.

### Versions or protocol do not match

Symptoms:

```text
the Hermes host runs Hermachy X.Y.Z; this client is 0.6.0. Upgrade both endpoints together
expected: hermachy-v2 <request>; upgrade the Hermachy client and server together
expected: hermachy-v1 <request>
server config has unknown fields: hermes_source_root; re-run hermachy-admin studio-finish for 0.6
```

The client and server versions differ: a 0.6.0 server refuses a 0.5.x client
with the `hermachy-v2` message, and a 0.5.x server answers a 0.6.0 client with the
`hermachy-v1` message. The last message means `server.json` was written by 0.5.x.
Install 0.6.0 on both machines as described in [Upgrade](#14-upgrade).

### Tool calls are refused

Symptom: a tool result such as `Hermachy routing unavailable: … Nothing was executed.`

The chat is bound to Omarchy, but its binding, the server configuration, or the
execution key cannot be read safely, or the workstation cannot be reached. Run
`hermachy --doctor` from the chat's Omarchy directory. On the remote host, check
that `~/.config/hermachy/server.json` and the execution private key are owned by
`REMOTE_USER` and not accessible to group or other users. Nothing runs on the host
instead.

### A TUI feature is unavailable in Hermachy

A message such as `complete.path is unavailable in Hermachy` means the bridge
refused a gateway method that would run or read on the Hermes host, move the
chat's workspace or profile, or send secrets to the client. This is expected; see
[SECURITY.md](../SECURITY.md#bridge-policy).

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

Run `hermachy` from an interactive local terminal with Node.js installed. The native TUI needs a local terminal; it reaches the remote gateway through Hermachy's loopback relay and an SSH channel without a remote PTY. Install the matching `tui/` assets alongside the client release.

If the TUI starts but cannot connect or keeps reconnecting, read
`~/.local/state/hermachy/logs/relay.log` on Omarchy. The relay writes SSH errors and
the remote bridge's messages there instead of drawing them over the TUI.

## 13. Revoke access

To block future Omarchy authentication to the remote Hermes host, run on the remote host:

```bash
~/.local/bin/hermachy-admin revoke --side studio
```

To block future remote-host authentication to Omarchy, run on Omarchy:

```bash
~/.local/bin/hermachy-admin revoke --side omarchy
```

The commands remove only Hermachy's marked `authorized_keys` blocks. They do **not** terminate already-authenticated TUI, bridge, or SSH sessions, including the gateway's SSH control connection to Omarchy.

Bound chats stay bound. Revoking `--side studio` stops new Hermachy connections,
but a bound chat continued from Hermes Desktop or another surface still runs its
tools on Omarchy until `--side omarchy` is revoked as well.

After revoking the relevant block:

1. Exit every active Hermachy TUI using this link.
2. On Omarchy, identify the exact `hermachy` client, its renderer, and its `ssh`
   channel processes. On the remote host, identify the `hermachy-server` bridge
   processes started for the client key and the plugin's SSH processes to Omarchy,
   whose control sockets are under `/tmp/hermachy-ssh-*`.
3. Terminate those exact processes without killing unrelated Hermes or SSH sessions.
   Do not stop the shared gateway unless you intend to interrupt the profile's
   other surfaces.
4. Verify no process or connection associated with the link remains.

Only then treat access as stopped. For permanent removal, delete the corresponding
dedicated key pair, JSON configuration, and installed Hermachy executables. On the
remote host, also remove `hermachy` from `plugins.enabled` in the default profile's
`config.yaml`, delete `~/.hermes/plugins/hermachy` and `~/.hermes/hermachy`, and
restart the gateway. Previously bound chats then run on the host like any other
chat of the profile.

## 14. Upgrade

Upgrade the host's Hermes, both Hermachy endpoints, and the plugin as one set. To
upgrade from 0.5.x:

1. Close active Hermachy clients so their 0.5.x gateways stop.
2. Update the remote host's main Hermes installation to v0.23.1 or later first,
   and confirm that `hermes --version` and `hermes gateway status` report it.
3. Build this 0.6.0 source checkout or verify and extract its archive using Step 5.
   Keep all four executables, `tui/`, and `plugin/hermachy/` together.
4. Transfer the client bundle and existing public handoff key as in Phase A,
   then re-run Phase B on Omarchy. This installs the 0.6.0 client and
   `hermachy-exec` and removes the stale `hermachy-gateway`.
5. Re-run Phase C on the remote host using the new server artifact and the new
   `studio-finish` arguments (`--hermes-command` and, if needed, `--hermes-home`;
   `--hermes-source-root` no longer exists). This rewrites `server.json`, installs
   and enables the plugin, and updates the client key's forced command. Managed
   files and marked key blocks update idempotently.
6. Restart the default profile's gateway once with `hermes gateway restart`.
7. Run `hermachy --doctor` from an Omarchy project directory, check the identities
   and access warnings, complete Step 11, then open a new chat or resume a saved
   conversation.

Chats from 0.5.x remain in the profile's session list but are not bound; resuming
one in Hermachy binds it. The 0.5.x runtime directories
`~/.local/share/hermachy/hermes-v*` are no longer used. Remove them after
verification, but keep one, together with the 0.5.2 executables, if you may roll
back to 0.5.2. The 0.5.2 installation procedure, including its pinned runtime,
remains in the repository history at commit `bedb4ad`. To roll back, remove
`hermachy` from `plugins.enabled`, restart the gateway, and reinstall 0.5.2 using
that procedure.

Treat Hermachy's client, server, plugin, the host's Hermes version, and SSH policy as one compatibility set. After a `hermes update` on the host that changes the gateway protocol, Hermachy refuses until a matching Hermachy release is installed. Do not unattended-auto-update one side of this security boundary.

The optional `hermachy-app` desktop interface was removed after 0.4.0. Launch
the native terminal interface with `hermachy` after upgrading.

## Security reference

Before granting access, read [SECURITY.md](../SECURITY.md). Anyone holding the Omarchy client private key can interact with the remote host's default Hermes profile, its chats, memory, and skills, and can have the agent run approved commands on the host through `hermes_host`. Anyone holding the remote execution private key can run non-interactive commands with `OMARCHY_USER` permissions. Protect both accounts and keys accordingly.
