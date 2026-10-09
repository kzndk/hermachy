# Changelog

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
