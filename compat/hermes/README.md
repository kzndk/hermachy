# Install the compatible Hermes runtime

Hermachy's supported source recipe is an immutable public Hermes commit plus
the patch shipped in this directory. It does not require access to a private
fork or an unreleased upstream capability.

| Component | Pinned reference |
|---|---|
| Hermes base (0.22.2 / v0.22.2) | [NousResearch/hermes-agent at `d923a5eb487a17c0a68801bba9ef0f56c81040a8`](https://github.com/NousResearch/hermes-agent/commit/d923a5eb487a17c0a68801bba9ef0f56c81040a8) |
| Compatibility changes | [safe-executor-v2.patch](safe-executor-v2.patch) |
| Source and patch checksums | [manifest.json](manifest.json) |
| Runtime Python | 3.14 (Hermachy's zipapps still require 3.9+) |
| Dependency installer | Hermes PM, using the base commit's `uv.lock` |

The runtime and bundled TUI are based on the same stable Hermes release. This
includes the native live goal/queued-prompt display, current model catalogs, and
upstream TUI fixes. Desktop-only features require the upstream Hermes Desktop.

The patch includes the complete Hermachy safe-executor v2 implementation,
configuration and tool routing, dedicated SSH identity and no-sync enforcement,
TUI gateway restrictions, and regression tests. It also keeps the launcher route
authoritative under scoped terminal configuration and prevents SSH environment
passthrough from forwarding remote profile secrets to Omarchy. Dependency
installation is disabled during protected Hermachy sessions. The upstream source and patch
are MIT licensed; see [LICENSE](LICENSE).

## 1. Prepare a separate source directory on the remote Hermes host

Run as the normal account that will own Hermes. You need Git, a shell, Python
3.9+ for source preparation, and outbound HTTPS access to GitHub and package
servers. Hermes's activation script provisions the Python 3.14 build toolchain.

Start in this Hermachy 0.5.0 checkout or the extracted local build archive.
Keep the entire checkout or bundle together; 0.5.0 is not yet a published tag.

Record this directory so later checks work after changing to the Hermes tree:

```bash
HERMACHY_INSTALL_SOURCE="$PWD"
python3 compat/hermes/prepare.py "$HOME/.local/share/hermachy/hermes-v0.22.2"
```

The helper fetches the exact public commit, checks the patch's SHA-256, applies
it, and verifies the entire resulting Git tree against the manifest. It refuses
an existing destination. Your existing Hermes installation and profile are not
modified by source preparation. If a download fails, choose a new empty path or
remove only the incomplete directory before retrying.

For manual inspection, the equivalent Git operations are a depth-one fetch of
the full commit above, detached checkout, then `git apply --index --check` and
`git apply --index` with this patch. The helper adds checksum and result-tree
verification. The patch deliberately remains staged so later checks can verify
both the index and working files; do not commit it or run `git pull` here.

## 2. Install locked dependencies into the required `venv/`

Use Hermes's own package manager to build a fresh environment. Keep the build
profile separate from your existing Hermes settings. The subshell keeps these
build-only environment variables out of your normal shell:

```bash
(
  cd "$HOME/.local/share/hermachy/hermes-v0.22.2"
  export HERMES_HOME="$HOME/.local/share/hermachy/build-profile"
  export HERMES_RUNTIME_DIR="$HOME/.local/share/hermachy/build-tools"
  source ./activate
  python3 -m pm.build_env --source . --out venv
)
cd "$HOME/.local/share/hermachy/hermes-v0.22.2"
```

If Python 3.14 is already available, activation is optional: run the PM build
with that interpreter and the same isolated build environment variables.
`--out venv` is required because Hermachy selects `venv/bin/python` and
`venv/bin/hermes` from this source tree. PM uses the committed lock and refuses
an existing output directory. Do not use raw pip or uv to mutate this runtime.

Choose optional dependencies before building: add `--extra anthropic` for native
Anthropic, or `--extra all` for the upstream optional feature set. Protected
sessions reject missing optional dependencies instead of installing packages
mid-session. To change the selection, build a new runtime directory and switch
endpoints after verification.

## 3. Configure and check Hermes locally

Configure your model and credentials on this remote host only. Hermachy's
supported setup uses the account's **default** Hermes profile at `~/.hermes`.
Do not set a custom `HERMES_HOME` in the SSH server's environment for this setup.
An existing non-default active profile is not a substitute for configuring the
default profile.

From the Hermes source directory, run setup only if the default profile still
needs configuration:

```bash
venv/bin/hermes --profile=default setup
```

`setup` modifies the default profile. If it is already configured, skip that
command and keep its settings. Then check the runtime:

```bash
venv/bin/hermes --profile=default --version
venv/bin/hermes --profile=default doctor
venv/bin/python -c 'from tools.environments.ssh import HERMACHY_CONTROL_VERSION; assert HERMACHY_CONTROL_VERSION == "2"; print("Hermachy safe-executor v2: OK")'
```

Hermes doctor also checks optional integrations and may report features you have
not installed. Resolve errors for your chosen provider and tools using the
locked PM build above. The final `hermachy --doctor` check in the setup
guide verifies the Hermachy connection separately.

Verify source provenance using the directory recorded in Step 1:

```bash
python3 "$HERMACHY_INSTALL_SOURCE/compat/hermes/prepare.py" --check "$HOME/.local/share/hermachy/hermes-v0.22.2"
```

In a new shell, set `HERMACHY_INSTALL_SOURCE` to that same absolute checkout or
bundle path first. Source verification checks the base revision, patch checksum,
Git tree, tracked working files, and unexpected unignored files. It does not
audit ignored files such as the virtualenv or installed package contents. The
import probe checks that the installed runtime loads capability v2. Neither
substitutes for behavioral tests or the two-machine SSH checks.

Continue with [the two-machine setup guide](../../docs/OMARCHY_REMOTE_HERMES.md#3-record-and-verify-both-tailscale-identities).
At `studio-finish`, use:

```text
--hermes-source-root "$HOME/.local/share/hermachy/hermes-v0.22.2"
```

Finish by running `hermachy --doctor` on Omarchy before opening the TUI.

## Validation and upgrades

The compatibility CI job prepares this source from the public upstream commit, installs locked
dependencies, checks the real capability import, and runs the supplied Hermes
contract tests on Linux and macOS. To reproduce the contract tests locally,
include `--group dev --group test` when building the fresh environment, then
run from the Hermes checkout.
The open-file limit applies only to this shell and its tests; the upstream gateway
suite opens many temporary databases:

```bash
ulimit -n 4096
HERMES_PYTHON="$PWD/venv/bin/python" scripts/run_tests.sh -j 4 \
  tests/hermes_cli/test_hermachy_safe_executor.py \
  tests/tools/test_hermachy_safe_executor_ssh.py \
  tests/tools/test_hermachy_surface_propagation.py \
  tests/tools/test_ssh_environment.py \
  tests/tools/test_ssh_bulk_upload.py \
  tests/tools/test_terminal_scope_multiplex.py \
  tests/gateway/test_hermachy_executor_bridge.py \
  tests/hermes_cli/test_env_loader.py \
  tests/hermes_cli/test_config_env_expansion.py \
  tests/tools/test_terminal_env_bridge.py \
  tests/tools/test_file_tools_container_config.py \
  tests/pm/test_hermachy_install_policy.py \
  tests/pm/test_lazy_install_policy.py \
  tests/tui_gateway/test_hermachy_boundary.py \
  tests/tui_gateway/test_hermachy_eof_shutdown.py \
  tests/tui_gateway/contracts/test_generated.py \
  tests/tui_gateway/test_protocol.py \
  tests/tui_gateway/test_tui_gateway_server.py \
  tests/agent/test_prompt_builder.py
```

The source and locked dependency recipe are reproducible; platform-specific
wheels and Python builds are not claimed to be byte-identical across machines.
The tests do not run a paid model conversation or establish your Tailscale/SSH
connections. Those connections are checked by the setup guide's doctor step.

Keep this runtime pinned, even if `hermes --version` reports an available update.
`hermes update`, a stock Hermes reinstall, or an
arbitrary branch update can remove the required behavior. For a future Hermachy
compatibility update, prepare the new recipe in a new directory, verify it,
close active clients, and rerun `studio-finish` with its source root. Keep the
previous directory for rollback. Never add a capability constant or disable the
server check to make an incompatible runtime start.
