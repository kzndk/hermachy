# Install the compatible Hermes runtime

Hermachy's supported source recipe is an immutable public Hermes commit plus
the patch shipped in this directory. It does not require access to a private
fork or an unreleased upstream capability.

| Component | Pinned reference |
|---|---|
| Hermes base (0.21.5 / v2026.9.24) | [NousResearch/hermes-agent at `f97608f178d1ffeca59860195ab7da295f7c8e5f`](https://github.com/NousResearch/hermes-agent/commit/f97608f178d1ffeca59860195ab7da295f7c8e5f) |
| Compatibility changes | [safe-executor-v2.patch](safe-executor-v2.patch) |
| Source and patch checksums | [manifest.json](manifest.json) |
| Runtime Python | 3.11 (Hermes requires 3.11–3.13; Hermachy's zipapps require 3.9+) |
| Dependency installer | uv 0.12.13, using the base commit's `uv.lock` |

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

Run as the normal account that will own Hermes. You need Git, Python 3.9+ with
venv/pip support, and outbound HTTPS access to GitHub and the Python package
downloads. On Debian/Ubuntu, install `git` and `python3-venv` if missing.

Start with a Hermachy checkout containing this guide. If you do not have one,
clone it on the remote host:

```bash
mkdir -p ~/src
git clone --branch v0.4.3 --depth 1 https://github.com/kzndk/hermachy.git ~/src/hermachy
cd ~/src/hermachy
```

If you already have a checkout, use the source at tag `v0.4.3` and `cd` to its
root instead. The [0.4.3 release bundle](https://github.com/kzndk/hermachy/releases/tag/v0.4.3)
includes this recipe; `cd` to the extracted bundle root when using it.

Record this directory so later checks work after changing to the Hermes tree:

```bash
HERMACHY_INSTALL_SOURCE="$PWD"
python3 compat/hermes/prepare.py "$HOME/.local/share/hermachy/hermes-safe-executor-v2"
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

These commands bootstrap the pinned installer separately and let uv download
Python 3.11 if it is missing:

```bash
python3 -m venv "$HOME/.local/share/hermachy/installer"
"$HOME/.local/share/hermachy/installer/bin/python" -m pip install 'uv==0.12.13'
cd "$HOME/.local/share/hermachy/hermes-safe-executor-v2"
UV_PROJECT_ENVIRONMENT=venv "$HOME/.local/share/hermachy/installer/bin/uv" sync --locked --python 3.11
```

`UV_PROJECT_ENVIRONMENT=venv` is necessary: Hermachy selects this source tree's
`venv/bin/python` and `venv/bin/hermes`. uv's default `.venv/` is not that layout.
`--locked` rejects a stale lockfile instead of silently changing dependencies.

The base dependencies support providers such as OpenRouter. Install any optional
provider/tool dependencies before starting Hermachy; for native Anthropic, repeat
the sync command with `--extra anthropic`. Repeat all chosen `--extra` options
on subsequent syncs, since uv removes unselected extras. Protected sessions
reject missing optional dependencies instead of installing packages mid-session.

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
locked dependency sync above. The final `hermachy --doctor` check in the setup
guide verifies the Hermachy connection separately.

Verify source provenance using the directory recorded in Step 1:

```bash
python3 "$HERMACHY_INSTALL_SOURCE/compat/hermes/prepare.py" --check "$HOME/.local/share/hermachy/hermes-safe-executor-v2"
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
--hermes-source-root "$HOME/.local/share/hermachy/hermes-safe-executor-v2"
```

Finish by running `hermachy --doctor` on Omarchy before opening the TUI.

## Validation and upgrades

The compatibility CI job prepares this source from the public upstream commit, installs locked
dependencies, checks the real capability import, and runs the supplied Hermes
contract tests on Linux and macOS. To reproduce the contract tests locally,
add `--extra dev` to your dependency sync, then run from the Hermes checkout.
The open-file limit applies only to this shell and its tests; the upstream gateway
suite opens many temporary databases:

```bash
ulimit -n 4096
scripts/run_tests.sh -j 4 \
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
  tests/tools/test_lazy_deps.py \
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
