# Changelog

## 0.6.0 — 2026-11-10

Hermachy now runs in the remote host's default Hermes profile on stock Hermes
v0.23.1 or later, sharing its chats with Hermes Desktop and the other surfaces.

- Require stock Hermes Agent v0.23.1 or later on the remote host, with the
  default profile's gateway running as its normal service. Hermes 0.23 allows one
  gateway per profile and its TUI attaches over a ticketed WebSocket, so the
  patched runtime, safe-executor v2 patch, `compat/hermes/` installer, and pinned
  `hermes-v*` runtime directory are removed.
- Share chats, memory, skills, and the session list with the profile's other
  surfaces. A new Hermachy plugin routes only chats that Hermachy created or
  resumed to Omarchy: their terminal, process, file, and code tools run over the
  dedicated execution key with strict host keys and no state sync or environment
  passthrough. A binding belongs to the chat, so a Hermachy chat continued in
  Desktop still runs on Omarchy. Other chats are untouched.
- Replace the terminal tool's `target` parameter with a `hermes_host` tool in
  new Hermachy chats. Chats started under 0.5.x keep working: their
  `terminal(target="hermes")` still runs on the host and `target="omarchy"` on the
  workstation. Refuse a tool call whose route cannot be established, and mark
  results after SSH loss or timeout `hermachy_outcome_unknown`; they are never
  re-run automatically.
- Refuse to attach unless the running gateway loaded the Hermachy plugin of the
  same version (checked with `plugins.list` on every attach and by the doctor), so
  a gateway that was not restarted can never run a Hermachy chat on the host.
- Add Omarchy project instructions and routing rules to Hermachy chats as a prompt
  section of up to 4000 characters, read with `hermachy-exec --context-files`.
- Connect the local TUI through a loopback WebSocket relay. Each (re)dial opens one
  restricted SSH channel to a bridge that attaches to the gateway with a
  single-use ticket. The bridge forwards only methods the pinned Hermes declares,
  refuses host execution, host file and path reads, workspace and profile changes,
  and bypass sessions, and rewrites `session.create`. Inline image previews,
  `@path` completion, and host file attachments are unavailable; images from
  Omarchy are uploaded as bytes.
- Move to protocol v2 (`hermachy-v2`) and refuse 0.5.x clients with an upgrade
  message. Remove `hermachy-gateway`: the release has four executables, `tui/`,
  and `plugin/hermachy/`. The client key's forced command no longer allows a PTY.
- `studio-finish` installs and enables the plugin, keeps its toolset on the
  `cli` platform only (Hermachy sessions use it; messaging surfaces never see
  `hermes_host`), and takes `--hermes-command` and `--hermes-home` instead of
  `--hermes-source-root`. `hermachy --doctor` checks
  the host's Hermes version, a real attach to the gateway, and the installed and
  enabled plugin version. The client refuses a host with a different Hermachy
  version or transport.
- Pass Hermes launch options such as `--model`, `--provider`, `--skills`, and
  `--reasoning` to new chats; refuse `--safe-mode` and `--ignore-user-config`.
  `--continue` prefers the latest Hermachy chat for the current Omarchy directory.
- Update the native TUI and CLI parser to Hermes v0.23.1 (`3b421578`), built with
  Hermes's pinned pnpm, keeping the activity-emoji and Omarchy-theme patches.

### Upgrade to 0.6.0

Update the remote host's main Hermes installation to v0.23.1 or later first. Then
install 0.6.0 on both machines: re-run `omarchy-install` on Omarchy and
`studio-finish` with its new arguments on the remote host, restart the gateway
once with `hermes gateway restart`, and run `hermachy --doctor`. The 0.5.x
`~/.local/share/hermachy/hermes-v*` runtimes are no longer used; remove them after
verification, keeping one with the 0.5.2 executables if you may roll back. See the
[upgrade guide](docs/OMARCHY_REMOTE_HERMES.md#14-upgrade).

## 0.5.2 — 2026-10-19 (local build)

- Update the Hermes runtime, native TUI, and CLI parser to stable v0.22.2.
- Preserve the complete safe-executor v2 patch, workspace context, activity icons, and Omarchy theme integration.


## 0.5.1 — 2026-10-14 (local build)

- Fix the `include_session_override` error when Hermes 0.22 builds a turn's
  system prompt or lists context files. Honor its launch-artifact option while
  keeping project instructions on the Omarchy workstation.
- Test full prompt assembly and context listing against the pinned Hermes
  runtime on Linux and macOS, alongside the isolated workspace helper tests.

## 0.5.0 — 2026-10-14 (local build)

- Upgrade the pinned Hermes runtime, native TUI, and CLI parser to v0.22.0 at
  `e06df9c2eae6901f4c88f464e0ae6e753c0428b8`. Build the Python 3.14 runtime
  through Hermes PM using its committed dependency lock.
- Port the protected SSH route to persistent-shell and file-sync controls,
  retain profile-aware session handling, and disable PM lazy installs during
  protected sessions. Preserve Omarchy themes and activity icons in the new TUI.
- Support `hermachy --native` (also `--tui-native`) for Hermes 0.22's native
  terminal scrollback mode.
- Flush sessions and stop lingering startup workers when the local TUI closes.
  Fetch and verify the pinned release tag so diagnostics report Hermes 0.22.0.

- Follow Omarchy's active palette from the first frame and update open TUIs on
  theme changes without restarting sessions. Preserve remote skin branding and
  retain the last valid palette while Omarchy replaces its theme directory.
- Read project instructions from the Omarchy workspace, preserving file priority,
  inherited AGENTS rules, subagent context, and the context-file listing. Prevent
  nonexistent Linux paths from loading Hermes's own developer instructions on
  the remote host. Keep the profile's SOUL, memory, and skills on the host.
- Show activity emojis for skills, browser actions, terminal commands, and other
  tools in the local TUI, including prepared, running, and completed calls.
  Preserve connector icons and delegation controls in resumed conversations.
- Include the renderer source patch and verify its checksum in reproducible builds.

## 0.4.3 — 2026-10-09

This release includes the changes recorded under 0.4.1 and 0.4.2 below; those
versions were developed locally and were not published as GitHub releases.

- Add `hermachy-exec` as the workstation SSH entry point. Refresh local desktop
  session settings and command paths on each execution, and prevent shell
  snapshots from restoring stale display settings after a compositor restart.
- Add explicit administration through the desktop polkit dialog, preserving
  command arguments and exit status and cleaning up pending authorization when
  the caller cancels. No passwordless policy is installed.
- Extend diagnostics and startup warnings to distinguish working SSH from
  available desktop, Wayland, user service bus, and administration access.
- Include the workstation entry point in reproducible builds and installation.
- Prevent Hermes's generic retry loop from repeating a workstation command
  after an execution exception. Report SSH loss and timeouts as uncertain
  outcomes requiring inspection before retrying. Keep sudo password handling
  out of the remote process.
- Resolve native file-tool paths on the workstation. The Mac Hermes host's
  `/tmp` symlink and home directory must not rewrite Linux file destinations.
- Update the download, installation, diagnostics, and upgrade guides for the
  complete five-executable release and its pinned Hermes runtime.

### Upgrade to 0.4.3

Close active clients and upgrade both Hermachy endpoints and the pinned Hermes
runtime together using the [upgrade guide](docs/OMARCHY_REMOTE_HERMES.md#14-upgrade).
Re-run the Omarchy installation phase so the execution key uses `hermachy-exec`,
then run `hermachy --doctor` before reopening sessions. The optional
`hermachy-app` interface has been removed; use the native TUI with `hermachy`.

## 0.4.2 — 2026-09-30

- Align the bundled native TUI, CLI parser, and protected remote runtime with
  Hermes 0.21.5 (`v2026.9.24`, `f97608f178`). This includes the live goal and
  queued-prompt display and the release's native TUI improvements.
- Rebase safe-executor v2 onto the reorganized gateway and tool modules. Preserve
  the dedicated identity, no state synchronization, pinned workspace, and
  execution routing under the new scoped terminal configuration.
- Disable SSH environment passthrough in protected sessions so remote profile
  credentials stay on the Hermes host. Update the keepalive wrapper for the new
  SSH method signature and the standalone parser's config-path help.
- Remove the optional `hermachy-app` desktop interface, its installer, Python
  bridge, and desktop tests and CI job. Hermachy retains the native Hermes TUI.

## 0.4.1 — 2026-09-12

- Update the native Hermes TUI and CLI parser to upstream `53c57871` (2026-09-12),
  including subagent controls, input handling, Markdown, and terminal layout fixes.
  Refresh renderer provenance and release checksums together.
- Add a public, pinned Hermes installation recipe with the complete safe-executor
  v2 patch, source and patch verification, locked dependencies, and Linux/macOS
  compatibility CI. Include the installer and setup docs in new release bundles.

## 0.4.0 — 2026-09-09

Hermachy now reconnects to the same remote agent after a temporary SSH loss and
includes an optional desktop app for Omarchy.

- Keep the remote gateway alive for up to one hour while disconnected.
  Acknowledgements and sequence numbers prevent duplicate RPC delivery during
  reconnection, with bounded buffers and a persistent marker that prevents
  replay into a replacement agent after a crash or reboot.
- Choose `target="omarchy"` or `target="hermes"` in the native terminal tool.
  Omarchy retains background/process support; Hermes supports approved foreground
  commands up to 300 seconds. Native file/code tools retain their Omarchy route.
- Add SSH keepalives to both connections and bounded gateway shutdown.
- Add the Hermachy desktop app: streaming Markdown chat, saved conversations,
  file/image attachments, approval and clarification dialogs, workspace switching,
  a read-only Git changes pane, and three animated companions.
- Follow the active Omarchy theme live, preserving drafts and open dialogs during
  theme changes. Selected text stays readable with inverted selection palettes
  such as Last Horizon. The desktop renderer uses Electron sandboxing, context isolation,
  restricted IPC, and sanitized Markdown.
- Align CLI and desktop versions at 0.4.0, add desktop unit/UI checks to CI, and
  provide a reproducible CLI release bundle with SHA-256 checksums.

### Upgrade

Upgrade both Hermachy endpoints together. Close active clients first, replace the
four executables and bundled TUI assets using the
[setup and upgrade guide](docs/OMARCHY_REMOTE_HERMES.md), then run
`hermachy --doctor`. The optional desktop installer was included in the 0.4.0
source release and has since been removed from the repository.

This remains a public pre-alpha requiring a tested Hermes build with
safe-executor capability v2. Stock Hermes builds without that contract are
rejected. Intentional client closure stops its gateway; after a remote reboot or
gateway crash, resume the saved conversation in a new connection. If the separate
Omarchy execution SSH connection drops during a command, inspect its outcome
before retrying. A full one-hour network outage soak is not part of this release's
validation.
