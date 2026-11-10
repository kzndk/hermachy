# Hermachy 0.6 design: attach to the profile's shared Hermes gateway

**Status:** proposal, not implemented. Hermachy 0.5.2 with its pinned Hermes
v0.22.2 runtime remains the supported release. Nothing in this document is
deployed.

Upstream references are paths in
[NousResearch/hermes-agent at v0.23.1](https://github.com/NousResearch/hermes-agent/tree/v0.23.1)
(`3b421578588243f920886e6fe79a6faa87ea026e`). Line numbers are approximate and
were read from that tag.

## 1. Why 0.5.2 cannot just move to Hermes 0.23

Hermachy 0.5.x runs a **private, protected gateway process** per connection.
Its environment is locked by the forced-command launcher (`TERMINAL_ENV=ssh`,
dedicated key, pinned cwd, `TERMINAL_SYNC_REMOTE_STATE=false`,
`HERMACHY_REQUIRE_SAFE_EXECUTOR=1`). The patched Hermes tree fails closed if
any of that is missing. The native TUI on Omarchy reached it by spawning
"Python" (`hermachy-gateway`) over stdio.

Hermes 0.23 breaks all three assumptions:

| Change in 0.23 | Where | Effect on 0.5.x |
|---|---|---|
| One gateway owns a profile home; the stdio entry claims `gateway.lock` and exits beside a live gateway | `tui_gateway/entry.py` (`_claim_launch_home`), `gateway/runtime_ownership.py` | A protected Hermachy gateway on the default profile is refused while the profile's normal gateway runs |
| The Ink TUI no longer spawns a stdio gateway. It runs `gateway_bootstrap.py` for a ticketed attach grant and dials a WebSocket, or dials `HERMES_TUI_GATEWAY_URL` | `ui-tui/src/localGatewayBootstrap.ts`, `apps/shared/src/node/gateway-client.ts` | `hermachy-gateway`, which accepts only `-m tui_gateway.entry`, is never invoked |
| All surfaces of all profiles share one multiplexed gateway process | `gateway/run.py` (`multiplex_profiles`, default on) | Process-wide monkeypatches and environment locks would affect every other session in that process |

## 2. What was ported and tested for v0.23.1

The mechanical port exists and is kept under
[`hermes-0.23/`](hermes-0.23/README.md) for reference. It is **not** a usable
release, because of the first two rows above.

- `safe-executor-v2` rebased onto v0.23.1 had three conflicts, in `gateway/run.py`, `hermes_cli/config.py` and
  `hermes_cli/env_loader.py`. Upstream's new handling of an unreadable `.env` was carried into the routed
  `reload_env`. Two new RPC methods are blocked in Hermachy mode: `image.render`, which reads host paths, and
  `vault.preview.fill`, which sends saved login fill scripts to the client.
  The full Hermachy contract suite passes on Linux: 934 passed, 0 failed, 12 skipped.
  Hermachy's own suites also pass: 123 unit tests, plus 3 prompt-integration tests against the port.
- The renderer patches (activity emojis, Omarchy theme) apply to v0.23.1 with one import conflict. The TUI typechecks,
  the patch tests pass 22/22, and the full upstream TUI suite passes 1985/1985. Note that v0.23 builds with
  Hermes's pinned **pnpm** (`pm/lock.json`), not `npm ci`.

## 3. Invariants to keep

These are the safe-executor v2 guarantees that any 0.6 design must preserve:

1. Every terminal, file, code and background-process tool of a Hermachy session executes on the
   Omarchy workstation unless the model explicitly chooses the Hermes host.
   No silent fallback to the host.
2. Execution uses the dedicated `hermachy_to_omarchy` identity only: `IdentitiesOnly`, no agent forwarding,
   strict host keys, forced command `hermachy-exec` on Omarchy.
3. No Hermes state, skills or credentials are synced to the workstation.
4. The session's workstation cwd is fixed by the launching client and cannot be changed by RPC.
5. A command whose outcome is unknown after SSH loss is never re-run automatically.
6. Host-side file reads triggered by client RPCs are refused.
   Paths in a Hermachy session name Omarchy files.
7. Fail closed: if the boundary cannot be established, refuse the session.

## 4. Constraints found in Hermes 0.23.1

| Concern | v0.23.1 behaviour | Consequence |
|---|---|---|
| Terminal routing granularity | `LocalSessionPolicy.terminal_json` is copied from the **profile** at `session.create` (`gateway/session_policy.py` `build_policy`, `tools/terminal_scope.py`). It is re-copied from the profile on resume (`gateway/session_policy_refresh.py`). | Routing is per profile, not per session. |
| Client influence | No create field selects the backend or SSH host. But `safe_mode` / `ignore_user_config` take `_bypass_policy` → `profile_terminal=False` → code defaults, i.e. `local` (`gateway/session_local.py`). `cwd_explicit` and `session.mutate workspace` can move `TERMINAL_CWD`. | Any attached client can drop a session onto the host backend unless a trusted layer refuses those fields. |
| Attach privilege | Grants are 30 s single-use tickets minted over the uid-checked control socket (`gateway/runtime_bootstrap.py`, `gateway/control_socket.py`). Every interactive WebSocket also gets `session:operator` (`gateway/run_api.py`). There are no restricted or session-scoped grants. | A bridged client is as privileged as a local TUI. Filtering must happen before frames reach the gateway. |
| Client locality | Native-ticket connections are stamped local (`native_bootstrap`). `client_env` values (`PATH`, venvs, nvm/conda, prefill file) are then applied to host tool subprocesses (`agent/session_client_env.py`). | A bridge must strip `client_env`. |
| Session cwd | `session.create` requires `cwd` to exist **on the host** (`gateway/session_policy.py`). | An Omarchy path cannot be passed as the session cwd. |
| Terminal backend plugins | `PluginContext.register_terminal_environment_provider` (`hermes_cli/plugins_context.py`), scoped per profile. The factory sends unknown backend names to `_build_plugin_env` (`tools/terminal_tool_backends.py`). Built-in names, including `ssh`, are reserved. `is_container` defaults to `True`, and with it `skip_container_guards`. | A `hermachy_ssh` provider is the supported hook. It must set `is_container=False` so dangerous-command approvals stay on. |
| Built-in SSH | `_build_ssh_env` never passes `sync_files`, so the profile is uploaded and synced back. The ControlMaster path is keyed by `user@host:port` only, not by identity, and is shared process-wide. `IdentitiesOnly` is not set (`tools/environments/ssh.py`). | Do not use the built-in `ssh` backend. Build `SSHEnvironment(sync_files=False)` with Hermachy's own options and control path. |
| Retries | `_run_foreground` still retries any `execute()` exception three times (`tools/terminal_tool.py`). | Uncertain-outcome conversion must live in the provider environment's `execute()`. |
| Per-session tools | `dynamic_schema_overrides` takes no session argument, and definitions are cached per profile (`tools/registry.py`, `model_tools.py`). Per-session toolsets are frozen at create. | Hermachy cannot reshape the core `terminal` tool per session. The host route needs its own tool name in a plugin toolset. |
| File-tool paths | For an ssh-classified environment, paths already resolve POSIX-style on the remote host (`tools/file_tools_paths.py`). This was also the case in v0.22.2. | `workstation_paths.py` is likely unnecessary, provided the provider's environment is classified as ssh. To verify. |
| Project instructions | Context files (AGENTS.md etc.) are discovered only on the host disk (`agent/system_prompt.py`, `agent/prompt_builder.py`). Plugin prompt sections are capped at 4000/8000 chars and placed after memory (`hermes_cli/plugins_dispatch.py`). | Workspace instructions from Omarchy need a plugin prompt section within the caps, or an upstream hook. |
| Approvals | Prompts route by session key. "always" decisions go into the **profile's** permanent allowlist and apply to all of its sessions (`tools/approval.py`). | A dedicated profile also isolates Hermachy's approval allowlist. |

## 5. Proposed architecture (recommended: a dedicated `hermachy` profile in the shared gateway)

```text
Omarchy                                   SSH (restricted key)          Hermes host
───────                                   ────────────────────          ───────────
hermachy ──▶ Ink TUI (pinned bundle)
             │ HERMES_TUI_GATEWAY_URL=ws://127.0.0.1:<port>/<token>
             ▼
        hermachy-gateway: loopback WS ⇄ lines ═══════════▶ hermachy-server (forced command)
                                                              • mints a ticket per dial (control socket)
                                                              • dials ws://127.0.0.1:<api>/api/ws
                                                              • RPC allowlist + session.create rewrite
                                                              ▼
                                                   shared profile gateway (launchd)
                                                     profile "hermachy" ── plugin "hermachy"
                                                       • terminal backend hermachy_ssh ──SSH──▶ hermachy-exec (Omarchy)
                                                       • tool hermes_host (explicit host route)
                                                       • workspace-instructions prompt section
                                                       • session guards (fail closed)
```

### 5.1 Omarchy: client and loopback relay

- `hermachy` launches the bundled TUI with `HERMES_TUI_GATEWAY_URL`. The URL points at a WebSocket listener
  that `hermachy-gateway` opens on `127.0.0.1`, on an ephemeral port, with an unguessable path token. It is
  a pure-stdlib RFC 6455 server, accepts a single client, and has no binary frames.
  The TUI then upgrades to the canonical wire itself when the gateway's `gateway.ready` carries
  `session_authority: true` (`adoptCanonicalAttach`).
- The relay maps one text frame to one line over the existing SSH transport. `json.dumps` never emits
  raw newlines. Each TUI (re)dial opens a new bridged attach, so the server can mint a fresh ticket.
  Lines must be unbounded, because `session.resume` returns full histories.
- `hermachy-exec`, the doctor, desktop access and polkit administration are unchanged.

### 5.2 SSH forced command: `hermachy-server` becomes a trusted bridge

- On each `gateway` request the bridge does the following:
  - It obtains a grant from the running profile gateway over the uid-checked control socket. This happens
    either through Hermes's own `attach_gateway` in the main install's runtime, or through a minimal
    client for the `session-ticket` verb.
  - It dials `/api/ws` with `["hermes-gateway-v1", "hermes-gateway-ticket.<t>"]`. It sends no `Origin`
    header and a matching `Host`, and raises the frame limit well above 8 MiB.
  - It relays frames and answers liveness promptly, because the server closes a socket that stalls for 30 s.
- The bridge is the **policy enforcement point**:
  - It uses an allowlist of canonical methods and refuses everything else.
  - On `session.create` it forces `profile=hermachy` and strips `client_env`. It refuses `safe_mode`,
    `ignore_user_config`, `cwd_explicit` and `toolsets` overrides, and binds the workstation cwd (§6.1).
  - It refuses `session.mutate workspace`, profile switching and the host-reading or secret-bearing methods
    already blocked in 0.5.x (`*.attach`, `clipboard.paste`, `shell.exec`, `cli.exec`, `image.render`,
    `vault.preview.fill`, …).
- The bridge prefers the already-running launchd gateway. If none is running it fails closed with a clear
  message instead of starting a daemon under sshd's minimal environment.

### 5.3 Hermes host: dedicated profile plus a Hermachy plugin

- **Profile `hermachy`** (name to be decided): `terminal.backend: hermachy_ssh`, `plugins.enabled: [hermachy]`.
  Its model and provider configuration matches the default profile; whether to copy or reference it is an
  open question. Because routing is per profile, the bypass and refresh behaviour in §4 can only ever
  re-select this profile's own backend. The bridge refuses the bypass fields, which would otherwise select
  `local`.
- **Plugin `hermachy`**, installed with `hermes plugins install` at a pinned revision:
  - `TerminalEnvironmentProvider` `hermachy_ssh`:
    - It wraps `SSHEnvironment(sync_files=False)` with the dedicated key, `IdentitiesOnly=yes`, a
      ControlMaster path keyed by identity, keepalives, and uncertain-outcome conversion in `execute()`.
    - It sets `is_container=False`.
    - It refuses to build an environment unless the session's bound workstation cwd and identity
      validate (fail closed).
  - Tool `hermes_host` in a plugin toolset enabled only by this profile. It is the explicit
    "run on the Hermes host" route, replacing the `target` parameter, and it runs guards as `local`.
  - A system-prompt section for workspace instructions read from Omarchy, within the caps. It is also a
    candidate for an upstream context-file hook.
  - `on_session_open` / `pre_tool_call` guards. They reject any session or tool call whose resolved backend
    is not `hermachy_ssh`, as defence in depth behind the bridge.
- The forced-command server no longer launches Hermes itself. There is no private gateway, no
  `gateway.lock` contention and no second writer to `state.db`.

## 6. Security model changes

- **Enforcement moves.** v2 pinned an entire process. In 0.6 the guarantees come from three layers:
  - profile-level backend pinning;
  - the plugin's fail-closed provider and guards;
  - the bridge's RPC allowlist.

  The Hermachy execution key becomes readable by the shared gateway process, which also runs other profiles
  and messaging surfaces. All of it was, and remains, one OS user, so there is no OS isolation either way.
  What is lost is the process-level "this process can only route to Omarchy" property.
- **Operator privilege.** Attach grants are full-privilege; there are no restricted grants upstream.
  The bridge must be an allowlist, not a denylist, and it must be re-reviewed for every Hermes upgrade.
- **Compatibility set.** The main install now determines the gateway protocol. Hermachy's TUI bundle, plugin
  and bridge must match the main install's Hermes version. `hermes update` on the host can therefore break
  Hermachy until a matching release exists. `runtime.describe` capabilities and the doctor must detect a
  mismatch and refuse.

### 6.1 Open gap: per-session workstation cwd

`session.create` requires a host-existing cwd. The ssh terminal cwd also comes from the profile, unless
`cwd_explicit` is set, and that flag requires the same host check. A dedicated profile alone therefore cannot
give each `hermachy` invocation its own Omarchy directory. Options, in order of preference:

1. Upstream: a per-session remote cwd for non-host backends.
2. The plugin keeps a bridge-written binding (connection ID → workstation cwd) and the provider reads it by
   session key. This is fail closed if missing. It needs a trusted channel from bridge to plugin, for
   example a mode-0600 file under the profile home.
3. One profile per workspace. Rejected: too heavy.

## 7. The alternative: stay in the default profile

Keeping Hermachy sessions in the **default** profile would share chats and memory with Hermes.app and other
surfaces. On v0.23.1 that is only possible by changing Hermes itself, through one of:

- a trusted, non-refreshable, non-bypassable per-session terminal policy that an owner-side actor can set; or
- patching the main install, i.e. carrying a fork.

It would also share the profile's "always" approval allowlist between Hermachy and every other surface. This
document recommends the dedicated profile, and recommends pursuing the upstream features in §8. If those
land, moving to the default profile becomes possible later.

## 8. Upstream feature requests

1. A per-session remote cwd for non-host terminal backends, without the host-existence check.
2. Restricted attach grants: a capability subset, a pinned profile, no operator.
3. A profile flag to refuse bypass sessions (`safe_mode` / `ignore_user_config`), or a bypass that keeps the
   profile's pinned terminal backend.
4. Context-file discovery through the session's terminal environment.
5. A ControlMaster path keyed by identity, and `IdentitiesOnly` for the built-in ssh backend.

## 9. Rollout

| Phase | Work | Exit criteria |
|---|---|---|
| 0 | Keep 0.5.2 / v0.22.2 in production. Publish this design and the reference port. | Doctor OK. No production changes. |
| 1 | Spike on a sandbox profile and sandbox `HERMES_HOME`: loopback relay, bridge, provider. | TUI attaches through SSH. Commands run on Omarchy. A bypass create is refused. Reconnect works. |
| 2 | Implementation and tests: bridge allowlist and rewrite unit tests; provider tests with a fake SSH; Linux and macOS CI against v0.23.x in a sandbox home; live check script. | All invariants in §3 covered by tests, including uncertain outcomes and host-path refusals. |
| 3 | Deploy: install the plugin into the `hermachy` profile and update the client and server. Version-match the main install. | `hermachy --doctor` checks the gateway version and capabilities. The 0.5.2 rollback (server config pointing at the v0.22.2 runtime) still works. |

## 10. Interim risk while on 0.5.2

The 0.5.2 runtime (v0.22.2) never claims `gateway.lock`. It writes the default profile's `state.db` beside a
newer main-install gateway. This has worked in practice, but upstream no longer supports it. Each main-install
upgrade increases the chance of a schema or behaviour mismatch. Keep the main install's upgrades deliberate
until 0.6 lands.

## Open questions

- Can Hermes Desktop open sessions of a non-default profile? This decides whether `session.shared_open`
  across surfaces still matters for Hermachy chats.
- How should the `hermachy` profile share model and provider configuration with the default profile?
- Bridge implementation: import Hermes's `attach_gateway` from the main install (coupled to its version) or
  speak the control-socket verb directly (a private protocol)?
- Should the TUI bundle be built from the host's own Hermes version on demand to guarantee a version match,
  given that the renderer patches are source-level?
