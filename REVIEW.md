# Installation compatibility verification — 2026-09-10

The supported Hermes recipe now pins public upstream commit
`8e0b9819d3be29e1628da4014c8065243402964b` plus the complete patch in
`compat/hermes/`. Its manifest records the patch SHA-256 and resulting Git tree.
The preparation helper fetched that commit without credentials and applied and
verified the patch in a new directory. A second fresh installation reproduced
the documented core-only `uv sync --locked --python 3.11` command with uv 0.12.13,
using Python 3.11.16 on Linux ARM64. Hermes's version command and capability
import both passed.

Validation:

- 90 Hermachy tests passed, including preparation from a Git remote, refusal to
  overwrite an existing installation, patch tampering and source drift rejection,
  and release packaging/checksum checks.
- 942 Hermes tests passed across the 12 contract and gateway files listed in the
  compatibility guide; one Windows-only test was skipped. The broader gateway
  test caught an undefined session ID in the initial patch's resume path; the
  patch was corrected and all 634 tests in that file passed on rerun.
- With only the documented core dependencies and a temporary empty profile,
  the actual Hermachy execution extension imported and the real gateway
  answered `gateway.capabilities`. A separate probe with test dependencies
  installed also verified protected `shell.exec` rejection. Neither probe
  created a model conversation or used a real execution SSH connection.
- Source verification, Python compilation, release checksum verification,
  documentation links, and whitespace checks passed.

Linux/macOS compatibility jobs are defined in CI; their hosted runs have not
been observed for these local changes. Existing historical releases do not
contain the new installer. The new source and future release bundles include
the recipe, patch, upstream license, and setup instructions.

---

# Hermachy 0.4.0 release verification — 2026-09-09

The release includes connection recovery, execution targets, and the optional
Omarchy desktop app. CLI and desktop both report 0.4.0. GitHub CI now covers
Python 3.9/3.11/3.13, desktop unit tests, browser UI tests, and the release
checksum check. `scripts/build_release.py` creates the complete versioned CLI
archive and its SHA256SUMS; tests verify archive completeness, executable modes,
reproducibility, checksum drift detection, and version agreement.

Local release validation passed:

- 88 Python tests, including real subprocess/socket recovery tests and rejection
  of malformed execution targets without executing on either machine.
- 7 desktop unit tests and 7 browser UI tests, including readable selected text
  on inverted selection palettes such as Last Horizon.
- Native Electron theme switching with an open dialog and preserved draft.
- Native Electron connection/rendering smoke check with sandboxing and context
  isolation enabled, Node integration disabled, and no renderer exceptions.
- Built client doctor, real gateway capability RPC, and clean EOF shutdown.
- A complete desktop model turn with a test text attachment and app icon: the
  agent read the Omarchy file, analyzed the image, and returned the expected
  token and color. This created a saved validation conversation.
- Python compilation, renderer syntax, runtime provenance, release checksums,
  and whitespace checks.

The earlier deployment record describes the installed endpoints; this release
review does not claim those binaries were replaced with the final release.
No full one-hour network-outage soak or interrupted execution-SSH command test
was added. The limitations below still apply except that a full interactive
desktop model/tool conversation has now been checked.

---

# Hermachy 0.4.0 — connection recovery and execution targets

The 0.4.0 update adds a private remote gateway supervisor and an acknowledged
stream around SSH. An attachment can reconnect without replacing the Hermes
agent. Sequence numbers deduplicate replayed input and output; bounded buffers
apply backpressure. A durable connection tombstone prevents replay into a fresh
agent after the supervisor terminates or the host reboots. Disconnected gateways
expire after one hour. Intentional close has bounded teardown.

Hermes's native terminal schema now accepts `target="omarchy"` (default) or
`target="hermes"`. Omarchy preserves native background/process support; Hermes
uses a separate local environment with the existing command approval checks,
foreground timeouts up to 300 seconds, and an explicit remote working directory.
File and code operations on Hermes use terminal shell/Python commands. The
protected Omarchy environment and no-sync settings remain active throughout.
Both SSH legs send keepalives. The extension ships inside Hermachy and requires
no edits to the installed Hermes source tree or vendor TUI bundle.

Validation: all 84 local tests pass, including real subprocess/socket tests for
lost acknowledgements, interrupted frames, a dropped connection during a command,
large output/backpressure, expiry, and graceful shutdown. Runtime tests cover
approval denial, invalid targets, native background support and no cross-machine
fallback. Live native terminal calls ran `uname -s` and `pwd` on each target and
verified Darwin on Hermes and Linux in the requested Omarchy workspace. The
candidate persistent gateway answered the real capability RPC over SSH and
closed cleanly. Build, compile, renderer syntax, and whitespace checks pass.

These checks do not cover a full interactive model conversation, a one-hour
network outage, or survival of a workstation command whose own execution SSH
connection is interrupted. Such commands must be inspected before retrying.
Deployment details and installed smoke results are recorded in `DEPLOYMENT.json`.

---

# Hermachy stability review — 2026-09-05

The reviewed release is **0.3.0**, prepared in `dist/`. The release includes the
previous crash and executor-v2 fixes. Machine-specific installation details,
backup locations and subsequent live verification are recorded separately in
the local `DEPLOYMENT.json` when deployed.

## Findings and fixes

| Priority | Finding | Result in this build |
| --- | --- | --- |
| High | Source/build drift: the checkout was 0.1.3, the installed native client was 0.2.0, and the builder omitted the gateway and TUI assets. Rebuilding could lose the crash and compatibility fixes. | Recovered the installed modules into this source tree, incorporated both previous fixes, and built all four executables plus the existing renderer and provenance. The build verifies renderer/parser checksums and tests reproducibility across timezones. |
| High | A known remote compatibility failure could reach the TUI and trigger repeated gateway launches. `--doctor` had no overall timeout. | A bounded remote/executor check now runs before the renderer starts. Malformed diagnostic responses fail cleanly. |
| High | The first shutdown fix still waited indefinitely when the SSH child ignored EOF or termination. | Shutdown closes stdin, allows a five-second grace period, sends TERM, and sends KILL after another five seconds if needed. The child is reaped. Signal and child exit statuses are preserved without daemon-thread I/O. |
| Medium | `session.create` with null/list/string parameters raised an uncaught AttributeError. Invalid input could report success. | Parameter shape is checked and malformed transport input returns a clean nonzero status. Large frames and partial writes retain their data. |
| Medium | Unqualified continuation could select a global TUI session before checking for a CLI session in the requested workspace. | Both surfaces are searched in the workspace before either global fallback. Session lookup has a server-side timeout and rejects malformed responses on the client. |
| Medium | Symlinked working directories could disagree with the executor's canonical path. Relative client config paths could resolve differently after `--worktree`. | The working directory and inherited client-config path are canonicalized locally. |
| Medium | An incomplete native release could replace a working client while leaving its gateway/assets missing or stale. Invalid server-runtime settings could replace a working server before validation failed. | Client release prerequisites and server configuration inputs are checked before replacing the corresponding executable. Regression tests verify preservation on these failures. |

Relevant implementation: [gateway](src/hermachy/gateway.py),
[client](src/hermachy/client.py), [server](src/hermachy/server.py),
[installer](src/hermachy/admin.py), [builder](scripts/build_zipapps.py).

## Validation

- All **70 unittest tests pass** on this Omarchy machine with Python 3.14.
- Real subprocess tests cover 20 repeated disconnects, open stdin, partial JSON,
  blocked pipes, large frames, malformed parameters, EOF and signals against a
  child that ignores termination. Core dumps are disabled for these tests.
- A built server runs against a disposable runtime/session database to verify
  workspace-first continuation through the actual forced-command entrypoint.
- Executor capability tests accept v2 and reject v1, unknown versions and failed probes.
- Build smoke tests, deterministic builds across three timezones, and bundled
  renderer/parser checksum validation pass. Node's renderer syntax check passes.
- A live check with the reviewed local gateway and the currently installed remote
  server passed: doctor, `kzn` resolution, gateway-capabilities JSON-RPC response,
  and clean EOF shutdown. It created no session and sent no model prompt.
- `git diff --check` passes.

Repeat local checks:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
python3 scripts/build_zipapps.py
node --check tui/entry.mjs
python3 scripts/live_check.py --session EXISTING_SESSION
```

## Scope and remaining limits

This is a review of Hermachy's Python integration, launch protocol, process lifecycle,
configuration validation, packaging and installation. The native renderer is the
existing vendor bundle with its original license/provenance; it was syntax-checked
and provenance-verified, not exhaustively audited. The remote Hermes
runtime and its existing uncommitted changes were inspected in the original
stability review. The subsequent integration rename is described below.

The review-stage live check used the previously installed remote server. The new
server's continuation change also passed an isolated executable integration test.
Post-deployment live checks are recorded in `DEPLOYMENT.json`.
No full model/tool turn, interactive rendering soak,
network-outage recovery soak, or exhaustive Hermes tool-routing audit was run.
These results support the specific fixes; they do not guarantee an absence of bugs.

The source tree includes native integration recovered from installed archives.
Use this maintained tree to rebuild the release. No upstream crash reports were made.

## Hermachy rename in 0.3.0

Commands, package imports, configuration and state paths, SSH key names, managed
key markers, environment variables, and the forced-command protocol use the
Hermachy name. Alias installation has been removed. The remote Hermes integration
must use the matching `HERMACHY_CONTROL_VERSION` and safe-executor guard.
Upgrade both endpoints together; previous endpoint namespaces are unsupported.
The doctor failure test now uses temporary UI fixtures instead of an installed
renderer, so it runs independently of machine setup.

Verification of 0.3.0: all 70 local tests and 307 focused remote Hermes tests
passed (two remote tests skipped). The broader gateway file passed 632 tests;
two failures also reproduce on the pre-rename baseline with the same existing
local edits. That comparison used a process-local open-file limit of 4096
because the Mac default of 256 exhausted descriptors during the broad suite.
Installed doctor, existing-session resolution, capability RPC, and EOF shutdown
passed after both endpoints were migrated. Existing remote edits were preserved.
