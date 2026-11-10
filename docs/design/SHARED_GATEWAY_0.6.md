# Hermachy 0.6 design: run in the default profile's shared Hermes gateway

**Status:** proposal, not implemented. Hermachy 0.5.2 with its pinned Hermes
v0.22.2 runtime remains the supported release. Nothing in this document is
deployed.

**Requirement:** Hermachy sessions live in the Hermes host's **default
profile**. They share chats, memory, skills and session history with every
other surface of that profile (Desktop, messaging, CLI). Only Hermachy
sessions execute on the Omarchy workstation.

Upstream references are paths in
[NousResearch/hermes-agent at v0.23.1](https://github.com/NousResearch/hermes-agent/tree/v0.23.1)
(`3b421578588243f920886e6fe79a6faa87ea026e`).

## 1. Why 0.5.2 cannot just move to Hermes 0.23

Hermachy 0.5.x runs a **private, protected gateway process** per connection.
The forced-command launcher locks its environment (`TERMINAL_ENV=ssh`, dedicated key,
pinned cwd, `TERMINAL_SYNC_REMOTE_STATE=false`, `HERMACHY_REQUIRE_SAFE_EXECUTOR=1`),
and the patched Hermes tree fails closed if any of that is missing. The native
TUI on Omarchy reached it by spawning "Python" (`hermachy-gateway`) over stdio.

Hermes 0.23 breaks all three assumptions:

| Change in 0.23 | Where | Effect on 0.5.x |
|---|---|---|
| One gateway owns a profile home. The stdio entry claims `gateway.lock` and exits beside a live gateway | `tui_gateway/entry.py` (`_claim_launch_home`), `gateway/runtime_ownership.py` | A protected Hermachy gateway on the default profile is refused while the profile's normal gateway runs |
| The Ink TUI no longer spawns a stdio gateway. It runs `gateway_bootstrap.py` for a ticketed attach grant and dials a WebSocket, or it dials `HERMES_TUI_GATEWAY_URL` | `ui-tui/src/localGatewayBootstrap.ts`, `apps/shared/src/node/gateway-client.ts` | `hermachy-gateway`, which only accepts `-m tui_gateway.entry`, is never invoked |
| All surfaces of all profiles share one multiplexed gateway process | `gateway/run.py` (`multiplex_profiles`, default on) | Process-wide monkeypatches and environment locks would affect every other session in that process |

## 2. What was ported and tested for v0.23.1

The mechanical port is kept under [`hermes-0.23/`](hermes-0.23/README.md) for
reference. It is **not** a usable release, because of rows 1 and 2 above.

**safe-executor v2**

- Rebased onto v0.23.1, with three conflicts.
- Two new RPC methods are blocked in Hermachy mode:
  - `image.render` reads host paths.
  - `vault.preview.fill` sends saved login fill scripts to the client.
- The Hermachy contract suite passes on Linux: 934 passed, 0 failed, 12 skipped.

**Renderer patches**

- The activity-emoji and Omarchy-theme patches apply with one import conflict.
- The TUI typechecks. The patch tests pass 22/22, and the full upstream TUI suite passes 1985/1985.
- v0.23 builds with Hermes's pinned **pnpm** (`pm/lock.json`).

## 3. Invariants to keep

1. Every terminal, file, code and background-process tool call of a Hermachy session runs on the
   Omarchy workstation, unless the model explicitly chooses the Hermes host. There is no silent
   fallback to the host.
2. Execution uses only the dedicated `hermachy_to_omarchy` identity. That means `IdentitiesOnly`,
   no agent forwarding, strict host keys, and the forced command `hermachy-exec` on Omarchy.
3. No Hermes state, skills or credentials are synced to the workstation.
4. The workstation cwd is fixed by the launching client and cannot be changed by RPC.
5. A command whose outcome is unknown after SSH loss is never re-run automatically.
6. Host-side file reads triggered by client RPCs are refused, because paths in a Hermachy session
   name Omarchy files.
7. Fail closed: if the boundary cannot be established, refuse the call or the session.
8. Sessions of the default profile that are not bound to Hermachy behave exactly as without Hermachy.

## 4. Constraints found in Hermes 0.23.1

| Concern | v0.23.1 behaviour | Consequence |
|---|---|---|
| Terminal routing | A session's `terminal_json` is copied from the profile at `session.create` and again on resume (`gateway/session_policy.py`, `gateway/session_policy_refresh.py`). Every terminal setting is read through a ContextVar scope (`tools/terminal_scope.py` `terminal_env`, `set_terminal_scope` / `reset_terminal_scope`). | The default profile stays `local`. A Hermachy session needs its own scope bound around its tool calls. |
| Plugin hooks | `register_terminal_environment_provider` is new in **v0.23.1**; v0.23.0 does not have it. `register_middleware("tool_execution")` wraps every agent tool call and receives `session_id`, `task_id`, `conversation_id`, `turn_id` (`hermes_cli/middleware.py`, `agent/tool_executor.py`, `agent/inline_tool_executors.py`). | A plugin in the default profile can route per session. The host's main install must be on v0.23.1 or later. |
| Middleware failure | A middleware callback that raises before calling `next_call` is **skipped**, and the tool runs anyway (`hermes_cli/middleware.py` `_run_execution_chain`). | The routing middleware must never raise. It returns an error result instead (fail closed). |
| Environment cache | Environments are cached under `session:<session key>`. A cached environment is reused whatever backend built it (`tools/terminal_tool.py` `_resolve_container_task_id`, `_acquire_env`). | Verified in a sandbox: a chat that ran locally and was then bound kept its **local** environment. The plugin must evict or refuse a cached environment that does not match the route. |
| Client influence | `safe_mode` / `ignore_user_config` create bypass sessions with code-default terminal settings (`gateway/session_local.py` `_bypass_policy`). Bypass ignores profile config, so plugins are not loaded. `cwd_explicit` and `session.mutate workspace` move `TERMINAL_CWD`. | The bridge must refuse these fields for Hermachy connections. |
| Attach privilege | Grants are 30 s single-use tickets from the uid-checked control socket (`gateway/runtime_bootstrap.py`, `gateway/control_socket.py`). Every interactive socket also gets `session:operator` (`gateway/run_api.py`). | A bridged client is as privileged as a local TUI, so filtering belongs in the bridge. |
| Client locality | Native-ticket connections are stamped local. `client_env` (`PATH`, venvs, prefill file) is applied to host subprocesses (`agent/session_client_env.py`). | The bridge strips `client_env`. |
| Session cwd | `session.create` requires a `cwd` that exists **on the host** (`gateway/session_policy.py`). | The workstation cwd travels in the Hermachy binding, not in the session cwd. |
| Built-in SSH | `_build_ssh_env` never disables `sync_files`. ControlMaster is keyed by `user@host:port`, and `IdentitiesOnly` is not set (`tools/terminal_tool_backends.py`, `tools/environments/ssh.py`). | Do not use the built-in `ssh` backend. The provider builds `SSHEnvironment(sync_files=False)` with its own options and control path. |
| Retries | `_run_foreground` retries any `execute()` exception three times (`tools/terminal_tool.py`). | Uncertain-outcome conversion lives in the provider environment's `execute()`. |
| Per-session tool schemas | Tool definitions are cached per profile; `dynamic_schema_overrides` gets no session (`tools/registry.py`, `model_tools.py`). | The core `terminal` schema stays stock. The host route is a separate tool, and per-session instructions go in a prompt section. |
| Project instructions | Context files are read from the host disk only (`agent/system_prompt.py`, `agent/prompt_builder.py`). Plugin prompt sections are capped at 4000/8000 chars (`hermes_cli/plugins_dispatch.py`). | Omarchy workspace instructions come from a bound-session prompt section, within the caps. |
| Approvals | "always" decisions go into the profile's permanent allowlist and apply to all its sessions (`tools/approval.py`). | In the default profile, Hermachy and the other surfaces **share** one allowlist. |

### 4.1 Sandbox proof (v0.23.1, Linux)

A throwaway plugin was loaded from a sandbox `HERMES_HOME` whose default backend is `local`. It
registered a `hermachy_ssh` provider (a stand-in environment) and a `tool_execution` middleware that
binds a `hermachy_ssh` scope for one bound session. Turns ran under gateway session keys, in one
process and one profile:

- The bound session's `terminal` ran in the provider's environment at the workstation cwd.
- `read_file` with a relative path read the workstation file.
- The unbound session stayed on `LocalEnvironment`, with separate cache keys.
- A session cached locally and then bound kept running locally. This is the cache hazard above, so
  the guard is required.

## 5. Proposed architecture

```text
Omarchy                                   SSH (restricted key)          Hermes host (default profile)
───────                                   ────────────────────          ─────────────────────────────
hermachy ──▶ Ink TUI (pinned bundle)
             │ HERMES_TUI_GATEWAY_URL=ws://127.0.0.1:<port>/<token>
             ▼
        hermachy-gateway: loopback WS ⇄ lines ═══════════▶ hermachy-server (forced command)
                                                              • mints a ticket per dial
                                                              • dials ws://127.0.0.1:<api>/api/ws
                                                              • RPC allowlist + session.create rewrite
                                                              • writes the session binding
                                                              ▼
                                                   shared gateway (launchd), default profile
                                                     plugin "hermachy":
                                                       • tool_execution middleware → hermachy_ssh scope
                                                       • provider hermachy_ssh ──SSH──▶ hermachy-exec (Omarchy)
                                                       • tool hermes_host (bound sessions only)
                                                       • bound-session prompt section
```

### 5.1 Omarchy: client and loopback relay

`hermachy` launches the bundled TUI with `HERMES_TUI_GATEWAY_URL`. That URL points at a WebSocket
listener inside `hermachy-gateway`:

- pure stdlib, RFC 6455;
- bound to `127.0.0.1` on an ephemeral port, with an unguessable path token;
- one client, text frames only.

The TUI upgrades to the canonical wire itself when the gateway's `gateway.ready` carries
`session_authority: true` (`adoptCanonicalAttach`).

Each text frame maps to one line on the SSH transport. Each TUI (re)dial opens a fresh bridged attach.
Lines are unbounded, because `session.resume` returns full histories. `hermachy-exec`, the doctor,
desktop access and polkit administration are unchanged.

### 5.2 SSH forced command: `hermachy-server` becomes a trusted bridge

Connection:

- It obtains a grant from the running gateway through the control socket.
- It dials `/api/ws` with `["hermes-gateway-v1", "hermes-gateway-ticket.<t>"]`: no `Origin`, a matching
  `Host`, and a large frame limit.
- It relays frames and keeps the link alive. The server closes a socket that is stalled for 30 s.
- If the launchd gateway is not running, it fails closed rather than starting one under sshd's
  environment.

Enforcement, by allowlist (not denylist):

- `session.create`:
  - forces the default profile and strips `client_env`;
  - refuses `safe_mode`, `ignore_user_config`, `cwd_explicit` and `toolsets` overrides;
  - sets `cwd` to an existing host directory.
- It refuses `session.mutate workspace` and profile switching.
- It refuses the host-reading or secret-bearing methods already blocked in 0.5.x (`*.attach`,
  `clipboard.paste`, `shell.exec`, `cli.exec`, `image.render`, `vault.preview.fill`, …).

Binding:

- Before forwarding a `session.create` or `session.resume` response, the bridge writes the binding
  `session_id → {workstation cwd, connection id}`.
- The binding is a mode-0600 file under the profile's Hermachy directory.
- The client cannot submit a turn before it receives the session id, so a bound session never runs a
  tool unbound.

### 5.3 Hermes host: the `hermachy` plugin in the default profile

The plugin is installed with `hermes plugins install` at a pinned revision and listed in the default
profile's `plugins.enabled`. It provides:

**Provider `hermachy_ssh`**

- Wraps `SSHEnvironment(sync_files=False)`.
- Uses the dedicated key, `IdentitiesOnly=yes`, a ControlMaster path keyed by identity, and keepalives.
- Converts uncertain outcomes in `execute()`.
- Sets `is_container=False`, so dangerous-command approvals stay on.
- Refuses to build an environment without a valid binding.

**`tool_execution` middleware**

- For a bound session it:
  - binds a `hermachy_ssh` scope with the binding's workstation cwd;
  - evicts any cached environment for the session key that the provider did not build;
  - then dispatches.
- For an unbound session it passes through untouched (invariant 8). If an unbound session finds a
  Hermachy environment cached, the middleware evicts it.
- It never raises; any internal failure returns an error result.
- It also covers subagents: their tool calls run with their own `session_id`, so the binding has to
  resolve through the parent lineage or `conversation_id`. This is still to be verified, as a
  required test.

**Tool `hermes_host`**

- The explicit "run on the Hermes host" route, replacing 0.5.x's `target` parameter.
- Runs guards as `local`.
- Refuses outside bound sessions, where the normal `terminal` already runs on the host.

**Prompt section** (bound sessions only)

- The routing instructions, plus Omarchy workspace instructions read over SSH, within the caps.

Because the plugin only acts on bound sessions, the forced-command server no longer launches Hermes
at all. There is no private gateway, no `gateway.lock` contention, and no second writer to `state.db`.

### 5.4 Routing semantics for a shared chat

A binding belongs to the **chat**. A bound chat runs its tools on Omarchy, whichever window submits
the turn. With `session.shared_open`, a Hermachy chat continued in Desktop still works in the Omarchy
workspace.

This differs from 0.5.x, where Desktop turns in the same chat ran on the host. Per-turn routing (by
submitting connection) is possible in principle, but it needs the turn's origin connection to be visible
to the middleware. That is an open question.

The plugin offers a slash command to unbind a chat.

## 6. Security model changes

**Where enforcement lives.** In 0.5.x, enforcement was a process-wide pin. In 0.6 it comes from:

- the bridge's allowlist and binding;
- the plugin's fail-closed middleware, cache guard and provider.

The execution key becomes readable by the shared gateway process, which also runs messaging surfaces.
It was one OS user before too, so no OS isolation is lost. What is lost is the process-level "this
process can only route to Omarchy" property.

**Default-profile consequences, accepted by the requirement in this document:**

- Hermachy shares the profile's "always" approval allowlist with all other surfaces.
- A bypass session (`safe_mode`) opened from another surface on a bound chat would not load the plugin
  and would run on the host. The bridge only prevents this for Hermachy connections.

**Privilege.** Attach grants carry full operator privilege. The bridge allowlist must be re-reviewed
for every Hermes upgrade.

**Compatibility set.** The main install's Hermes version now determines the protocol and plugin API.
The plugin, bridge and TUI bundle must match it, and the doctor must refuse on mismatch.

## 7. Upstream feature requests

These would harden the design but are not required for it:

1. Restricted attach grants: a capability subset, a pinned profile, no operator.
2. A plugin hook that can set a session's terminal policy at create time and keep it across resume
   refresh. This would replace the middleware and cache guard.
3. Middleware that fails closed: an opt-in for a callback whose failure must stop the call.
4. Context-file discovery through the session's terminal environment.
5. An identity-keyed ControlMaster path and `IdentitiesOnly` for the built-in ssh backend.

## 8. Rollout

| Phase | Work | Exit criteria |
|---|---|---|
| 0 | Keep 0.5.2 / v0.22.2 in production. Publish this design. | Doctor OK. No production change. |
| 1 | Upgrade the host's main install to v0.23.1. This is the owner's decision: it has local, uncommitted work. | Main gateway on v0.23.1. Other surfaces unaffected. |
| 2 | Spike against a **sandbox** `HERMES_HOME` and its own gateway on the host: relay, bridge, plugin, real SSH to Omarchy. | TUI attaches through SSH. Bound tools run on Omarchy. An unbound session stays local. Bypass is refused. Reconnect works. |
| 3 | Implementation and tests on Linux and macOS CI against v0.23.x in a sandbox home. Tests cover the bridge allowlist and rewrite, the middleware (including failure and cache-guard cases), subagent lineage, the provider (no sync, identity, uncertain outcomes), and host-path refusals. | Every invariant in §3 is covered by a test. |
| 4 | Deploy: install and enable the plugin in the default profile, restart the main gateway once, and update the client and server. | `hermachy --doctor` checks the gateway version, capabilities and plugin. Rollback means disabling the plugin and pointing the server back at the v0.22.2 runtime. |

## 9. Interim risk while on 0.5.2

The 0.5.2 runtime (v0.22.2) never claims `gateway.lock`. It writes the default profile's `state.db`
beside the newer main-install gateway. This has worked in practice, but upstream no longer supports
it, and each main-install upgrade increases the chance of a mismatch. 0.6 removes the second writer.

## Open questions

- Subagents: confirm how child sessions map to the parent's binding (lineage or `conversation_id`).
- Per-turn routing for shared chats (§5.4): is the submitting connection visible to tool middleware?
- Should a binding outlive its Hermachy connection (resume later from Desktop) or end with it?
- Bridge implementation: import Hermes's `attach_gateway` (coupled to the installed version), or speak
  the control-socket verb directly?
- Build the TUI bundle from the host's own Hermes version on demand, to guarantee a version match?
