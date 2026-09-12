# Changelog

## Unreleased

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
`hermachy --doctor`. Install or update the desktop app separately using
the [desktop guide](https://github.com/kzndk/hermachy/blob/v0.4.0/desktop/README.md).

This remains a public pre-alpha requiring a tested Hermes build with
safe-executor capability v2. Stock Hermes builds without that contract are
rejected. Intentional client closure stops its gateway; after a remote reboot or
gateway crash, resume the saved conversation in a new connection. If the separate
Omarchy execution SSH connection drops during a command, inspect its outcome
before retrying. A full one-hour network outage soak is not part of this release's
validation.
