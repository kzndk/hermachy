# Hermachy app

A desktop companion for the Hermachy CLI on Omarchy. The local app connects to the same remote Hermes agent and conversation store through the existing persistent SSH transport.

- A compact workspace layout inspired by [T3 Code](https://t3.codes/), with a conversation sidebar, focused chat, and a collapsible Git changes pane. Review tracked, staged, and untracked text changes without modifying the working tree.
- **Always follows the active Omarchy theme.** Backgrounds, text, accents, diff colors, dialogs, and companion artwork update live, including dark/light theme changes. There is no separate app color scheme to keep in sync.
- Three original, animated SVG companions: **Moss**, a forest spirit; **Ember**, a little dragon; and **Orbit**, a moon jelly. They react to thinking, tool execution, approvals, completion, and connection recovery. Companion selection and animation preference persist. System reduced-motion settings are respected.
- Streaming Markdown replies, expandable tool activity with execution targets, saved conversation search and resume, workspace selection, and Stop.
- Attach files and images with a native picker, drag and drop, or clipboard paste. Previews can be removed before sending. Rejected submissions keep the draft.
- Approval, clarification, password, and secret input dialogs; optional completion notifications.
- A normal desktop launcher, with no browser tab or local HTTP listener required.

## Install on Omarchy

Requires a working **Hermachy 0.4.0+** CLI connection, Python 3.9+, and Node 22.12+ to install the desktop runtime. Run `hermachy --doctor` first.

```bash
cd desktop
npm ci
npm exec install-electron
npm run install:desktop
hermachy-app
```

The installer places the app and its Electron runtime in `~/.local/share/hermachy/app`, creates `~/.local/bin/hermachy-app`, and adds **Hermachy** to the Omarchy app launcher. It honors `XDG_DATA_HOME` for application and launcher data. An earlier app installation is retained as `app.previous` until the next installation. The CLI and remote host installation are separate.

The installed launcher uses software rendering (`--disable-gpu`) to avoid Electron EGL initialization errors on virtual GPUs. This disables hardware acceleration for the app.

To launch in a project:

```bash
hermachy-app --cwd /absolute/path/to/project
```

For development, run `npm start -- --cwd /absolute/path/to/project` from this directory. Electron uses Wayland on supported desktops; `--ozone-platform=x11` can be used for an XWayland session.

## Omarchy theme integration

The app reads the active `colors.toml` from `~/.local/state/omarchy/current/theme`, using Omarchy's `omarchy-theme-color` resolver when available. XDG state directories and the older `~/.config/omarchy/current/theme` location are also supported. Semantic, short-name, and ANSI palettes are accepted. It watches the parent directory and checks for changes once a second, so an atomic theme-directory replacement, an in-place edit, or waking the computer updates the existing window. Theme updates preserve drafts, attachments, open dialogs, and running agent sessions.

The last valid palette stays active during incomplete writes or a temporary missing theme directory. If no theme has ever been available, the app uses a neutral fallback and says **System theme unavailable**. The integration only reads Omarchy's theme; it installs no hooks and does not modify desktop configuration. Companion colors come from the theme's green, orange, and magenta palette entries.

## Changes pane

Use **Changes** in the conversation header to open or close the Git review pane. Select a file to see its current diff, or use Refresh; an open pane also refreshes every 15 seconds. New text files are previewed up to 512 KiB, and external diff/textconv commands are disabled. The pane opens as an overlay on small windows. It does not stage, discard, commit, or edit files.

## Using attachments

Up to **10 files**, **8 MiB each**, **32 MiB total** per prompt. PNG, JPEG, GIF, and WebP are detected from their contents and uploaded with `image.attach_bytes`, so Hermes receives the image bytes, not a path on another computer. Vision support depends on the configured Hermes model. Other files, including PDFs, source files, and documents, are attached as byte-for-byte snapshots on Omarchy, with an explicit path for the agent's Omarchy tools. Reading a particular document format still depends on the tools installed on the workstation.

File snapshots remain available for saved conversations in the Electron user-data directory, normally `~/.config/Hermachy/attachments`. Unsent snapshots are removed when removed from the prompt or when the app quits normally. Sent snapshots remain until you remove them manually; deleting them makes those attachments unavailable to resumed sessions. Uploaded images also remain in the remote Hermes profile. Preferences live beside the local attachments in `preferences.json`. No extra provider keys or cloned Hermes profile are required.

## Connection behavior

Startup runs the same remote capability and executor checks as the CLI. Temporary SSH loss reconnects to the same gateway with acknowledged replay. Closing the app shuts down its gateway; saved conversations can be resumed from the sidebar on the next launch. A remote reboot or terminated gateway requires reconnecting and resuming the saved conversation. A timed-out submission is never automatically resent because its execution outcome may be uncertain.

The app currently runs one foreground conversation at a time. Stop the active response before switching conversations or workspaces. Model selection follows the configured Hermes profile; the current model is displayed in the composer.

## Validation

```bash
npm test
npm run test:ui
node scripts/electron-smoke.cjs
node scripts/theme-smoke.cjs
```

The UI tests use an isolated local test server with a simulated gateway. Locally
they use `/usr/bin/chromium` when present; otherwise install the bundled browser
with `npx playwright install chromium`. CI uses the bundled browser. Set
`HERMACHY_CHROMIUM` to select another Chromium executable. The Electron smoke
test uses the real CLI connection, checks the sandbox settings, and captures the
actual desktop window. `node scripts/live-probe.cjs` creates an empty session
and validates real image upload/detach without submitting a model prompt.
`node scripts/live-prompt.cjs` submits one small prompt with a test text file and
the app icon, checking the streamed response and real Omarchy file access; it
uses the configured model and leaves a saved validation conversation.

The theme smoke test swaps an isolated fixture theme while the real Electron window has a draft and companion picker open. It checks native window and renderer updates through the actual IPC and Omarchy resolver, without changing your desktop theme. Unit tests cover palette validation, legacy colors, missing directories, replacement, incomplete writes, and Git review confinement.

The renderer is sandboxed with context isolation and no Node integration. IPC methods and sender frames are checked, remote Markdown is sanitized, navigation is blocked, and external links are limited to HTTP(S), following [Electron's security guidance](https://www.electronjs.org/docs/latest/tutorial/security). All artwork is original code-native SVG; fonts and runtime dependencies are bundled locally.
