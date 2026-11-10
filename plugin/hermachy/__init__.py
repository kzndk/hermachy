"""Hermachy plugin: run bound default-profile sessions on the Omarchy workstation.

A session is *bound* when the Hermachy bridge (``hermachy-server`` on this host)
recorded it, or when its host working directory is a Hermachy connection
workspace. Every tool call of a bound session runs under a ``hermachy_ssh``
terminal scope, which selects this plugin's provider. The provider builds an SSH
environment on the dedicated execution identity, with strict host keys, no
agent, no forwarding, no profile synchronisation and no environment passthrough.
Unbound sessions are untouched.

Fail closed: Hermes skips a middleware callback that raises and runs the tool
anyway, so every failure here is returned as an error result instead.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import tempfile
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

PLUGIN_VERSION = "0.6.0"
BACKEND = "hermachy_ssh"
TOOLSET = "hermachy"
HOST_TOOL = "hermes_host"
BINDING_VERSION = 1
# Tools whose execution environment is the session's terminal backend.
ENVIRONMENT_TOOLSETS = frozenset({"terminal", "file", "code_execution"})
ENVIRONMENT_TOOLS = frozenset({
    "terminal", "process", "process_manage", "read_file", "write_file", "patch",
    "search_files", "execute_code",
})
DESKTOP_ENV = frozenset({
    "DISPLAY", "WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "XAUTHORITY",
    "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XDG_SESSION_TYPE",
    "XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION",
    "XDG_CONFIG_DIRS", "XDG_DATA_DIRS", "OMARCHY_PATH",
    "QT_QPA_PLATFORM", "QT_QPA_PLATFORMTHEME", "GDK_BACKEND",
    "ELECTRON_OZONE_PLATFORM_HINT", "MOZ_ENABLE_WAYLAND",
    "PULSE_SERVER", "PIPEWIRE_REMOTE",
})
_CONNECTION_RE = re.compile(r"[a-f0-9]{32}")
_SESSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")
_MAX_BINDING_BYTES = 16384
_FOREGROUND_ONLY = ("background", "pty", "notify", "notify_on_complete", "watch_patterns")
_UNCERTAIN = ("\nHermachy: command outcome is uncertain. Inspect the Omarchy processes and "
              "resulting state before retrying; do not rerun the command automatically or "
              "on the other machine.")


class RouteError(RuntimeError):
    """A session is bound to the workstation but its route cannot be established."""


@dataclass(frozen=True)
class Executor:
    host: str
    user: str
    port: int
    key: str


@dataclass(frozen=True)
class Route:
    connection: str
    cwd: str
    executor: Executor


_ROUTE: contextvars.ContextVar[Optional[Route]] = contextvars.ContextVar("hermachy_route", default=None)
# Session key -> connection, learned from bound calls. Subagents share their parent's session key
# but carry their own session id, so this is how their calls stay on the parent's route.
_KEY_ROUTES: dict[str, str] = {}
_KEY_LOCK = threading.Lock()


# ---------------------------------------------------------------- bindings and configuration

def hermachy_home() -> Path:
    from hermes_constants import get_hermes_home
    return Path(get_hermes_home()) / "hermachy"


def server_config_path() -> Path:
    configured = os.environ.get("HERMACHY_SERVER_CONFIG")
    return Path(configured).expanduser() if configured else Path("~/.config/hermachy/server.json").expanduser()


def _has_control(text: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in text)


def _read_private(path: Path, *, limit: int = _MAX_BINDING_BYTES) -> bytes:
    """Read a regular, user-owned, mode-private file without following a final symlink."""
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        raise RouteError(f"{path.name} is not a regular file")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise RouteError(f"{path.name} is not owned by the current user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise RouteError(f"{path.name} must not be accessible to group or others")
    if info.st_size > limit:
        raise RouteError(f"{path.name} is too large")
    with open(path, "rb") as stream:
        return stream.read(limit + 1)


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(_read_private(path).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RouteError(f"{path.name} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RouteError(f"{path.name} must contain an object")
    return payload


def load_executor() -> Executor:
    """The dedicated workstation endpoint from Hermachy's private server configuration."""
    try:
        payload = _read_json(server_config_path())
    except FileNotFoundError as exc:
        raise RouteError("Hermachy server configuration is missing") from exc
    host, user = payload.get("executor_host"), payload.get("executor_user")
    port, key = payload.get("executor_port", 22), payload.get("executor_identity_file")
    for value, label in ((host, "executor_host"), (user, "executor_user")):
        if not isinstance(value, str) or not value or any(c.isspace() for c in value) or _has_control(value):
            raise RouteError(f"invalid {label}")
    if type(port) is not int or not 1 <= port <= 65535:
        raise RouteError("invalid executor_port")
    if not isinstance(key, str) or not key.startswith("/") or _has_control(key):
        raise RouteError("invalid executor_identity_file")
    _read_private(Path(key), limit=65536)
    return Executor(host, user, port, key)


def _connection_cwd(connection: str) -> str:
    if not _CONNECTION_RE.fullmatch(connection):
        raise RouteError("invalid Hermachy connection id")
    try:
        payload = _read_json(hermachy_home() / "connections" / f"{connection}.json")
    except FileNotFoundError as exc:
        raise RouteError("Hermachy connection record is missing") from exc
    cwd = payload.get("cwd")
    if (payload.get("v") != BINDING_VERSION or payload.get("connection") != connection
            or not isinstance(cwd, str) or not cwd.startswith("/") or _has_control(cwd)
            or len(cwd) > 4096):
        raise RouteError("invalid Hermachy connection record")
    return str(PurePosixPath(cwd))


def connection_for_session(session_id: str) -> Optional[str]:
    if not session_id or not _SESSION_RE.fullmatch(session_id):
        return None
    path = hermachy_home() / "sessions" / f"{session_id}.json"
    try:
        payload = _read_json(path)
    except FileNotFoundError:
        return None
    connection = payload.get("connection")
    if payload.get("v") != BINDING_VERSION or not isinstance(connection, str):
        raise RouteError("invalid Hermachy session binding")
    return connection


def connection_for_cwd(cwd: str) -> Optional[str]:
    """``<hermachy home>/workspaces/<connection>`` is the host cwd of a session the bridge created."""
    if not cwd:
        return None
    try:
        path = Path(cwd)
        parent = hermachy_home() / "workspaces"
        if path.parent != parent and Path(os.path.realpath(path)).parent != Path(os.path.realpath(parent)):
            return None
    except (OSError, ValueError):
        return None
    name = path.name
    return name if _CONNECTION_RE.fullmatch(name) else None


def resolve_route(session_id: str = "", session_key: str = "", session_cwd: str = "") -> Optional[Route]:
    """The workstation route of a bound session, None for an unbound one, RouteError if broken."""
    connection = connection_for_session(session_id)
    if connection is None:
        connection = connection_for_cwd(session_cwd)
    if connection is None and session_key:
        with _KEY_LOCK:
            connection = _KEY_ROUTES.get(session_key)
    if connection is None:
        return None
    route = Route(connection, _connection_cwd(connection), load_executor())
    if session_key:
        with _KEY_LOCK:
            _KEY_ROUTES[session_key] = connection
    return route


# ---------------------------------------------------------------- execution environment

_WORKSTATION_CLASS = None


def workstation_environment_class():
    """``SSHEnvironment`` subclass for the protected route (built lazily inside Hermes)."""
    global _WORKSTATION_CLASS
    if _WORKSTATION_CLASS is not None:
        return _WORKSTATION_CLASS
    import subprocess
    from tools.environments.base import BaseEnvironment
    from tools.environments.base_output import _popen_bash
    from tools.environments.remote_common import bash_argv
    from tools.environments import ssh as ssh_module

    class WorkstationSSHEnvironment(ssh_module.SSHEnvironment):
        """Dedicated identity, private ControlMaster, no sync, no passthrough, uncertain outcomes."""

        _profile_scoped_passthrough = False
        _passwordless_sudo_probe_supported = False
        hermachy_route: Route

        def __init__(self, route: Route, timeout: int = 60):  # noqa: D401 - replaces SSHEnvironment.__init__
            BaseEnvironment.__init__(self, cwd=route.cwd, timeout=timeout, persist_env=True)
            self.hermachy_route = route
            executor = route.executor
            self.host, self.user, self.port, self.key_path = executor.host, executor.user, executor.port, executor.key
            self._sync_manager = None
            self._remote_home = ""
            self._remote_home_detected = False
            # /tmp keeps the socket under macOS's 104-byte sun_path limit; a fresh 0700 directory
            # keeps every other session and process off this master, even for the same host.
            self.control_dir = Path(tempfile.mkdtemp(prefix="hermachy-ssh-", dir="/tmp"))  # no-tmp: ok
            self.control_dir.chmod(0o700)
            self.control_socket = self.control_dir / "c.sock"
            try:
                ssh_module._ensure_ssh_available()
                self._establish_connection()
                self.init_session()
            except BaseException:
                self._shutdown_master()
                raise

        def _identity_options(self) -> list[str]:
            return ["-F", "/dev/null", "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
                    "-o", "PreferredAuthentications=publickey", "-o", "PubkeyAuthentication=yes",
                    "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
                    "-o", "GSSAPIAuthentication=no", "-o", "ClearAllForwardings=yes",
                    "-o", "ForwardAgent=no", "-o", "ForwardX11=no"]

        def _target_flags(self, port_flag: str) -> list:
            flags = [port_flag, str(self.port)] if self.port != 22 else []
            return flags + self._identity_options() + ["-i", self.key_path]

        def _build_ssh_command(self, extra_args: list | None = None, send_env=()) -> list:
            cmd = ["ssh", "-T"]
            if ssh_module._SSH_MULTIPLEX:
                cmd += ["-o", f"ControlPath={self.control_socket}", "-o", "ControlMaster=auto",
                        "-o", "ControlPersist=300"]
            cmd += ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
                    "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
            cmd += self._target_flags("-p")
            cmd += list(extra_args or [])
            cmd.append(f"{self.user}@{self.host}")
            return cmd

        def _control_sockets(self) -> list[Path]:
            return [self.control_socket]

        def _run_bash(self, cmd_string: str, *, login: bool = False, timeout: int = 120,
                      stdin_data: str | None = None):
            # Credentials and skill environment belong to the Hermes host, never the workstation.
            return _popen_bash(self._build_ssh_command() + bash_argv(shlex.quote(cmd_string), login), stdin_data)

        def _before_execute(self) -> None:
            return None

        def _prepare_command(self, command: str):
            # Root authentication belongs to polkit on the workstation (hermachy-exec --admin); never
            # cache or pipe a password, and never apply the host's macOS `open` rewriting remotely.
            return command, None

        def _snapshot_excluded_passthrough_names(self):
            # Desktop settings are refreshed by hermachy-exec on every call; keep them out of snapshots.
            return tuple(sorted(set(super()._snapshot_excluded_passthrough_names()) | DESKTOP_ENV))

        def _scp_upload(self, *_args, **_kwargs):
            raise RuntimeError("Hermachy never synchronizes Hermes state to the workstation")

        _ssh_bulk_upload = _ssh_bulk_download = _ssh_delete = _scp_upload

        def _workstation_cwd(self, cwd):
            """Never send a host-only directory (Hermachy's host workspace, the Hermes home, or the
            host user's home when it differs from the workstation's) as a workstation cwd."""
            if not isinstance(cwd, str) or not cwd.strip():
                return cwd
            text = str(PurePosixPath(cwd))
            host_only = [str(hermachy_home()), str(Path(hermachy_home()).parent)]
            host_home = str(Path.home())
            if not self.hermachy_route.cwd.startswith(host_home.rstrip("/") + "/") and self.hermachy_route.cwd != host_home:
                host_only.append(host_home)
            if any(text == root or text.startswith(root.rstrip("/") + "/") for root in host_only):
                return self.hermachy_route.cwd
            return cwd

        def execute(self, command, cwd="", *args, **kwargs):
            cwd = self._workstation_cwd(cwd)
            try:
                result = super().execute(command, cwd, *args, **kwargs)
            except Exception as exc:
                # Hermes retries execute() exceptions, which can occur AFTER a command changed the
                # workstation. Returning a failure avoids duplicate writes.
                result = {"returncode": 255, "output": f"Hermachy execution failed ({type(exc).__name__})."}
            if result.get("returncode") in (124, 255):
                result = dict(result)
                result["hermachy_outcome_unknown"] = True
                result["output"] = (result.get("output") or "") + _UNCERTAIN
            return result

        def _shutdown_master(self) -> bool:
            confirmed = True
            if ssh_module._SSH_MULTIPLEX and self.control_socket.exists():
                try:
                    done = subprocess.run(self._build_ssh_command(["-O", "exit"]), capture_output=True,
                                          text=True, timeout=10, stdin=subprocess.DEVNULL, check=False)
                    confirmed = done.returncode == 0
                except (OSError, subprocess.SubprocessError):
                    confirmed = False
            if confirmed or not self.control_socket.exists():
                for remove in (self.control_socket.unlink, self.control_dir.rmdir):
                    try:
                        remove()
                    except (FileNotFoundError, OSError):
                        pass
            else:
                logger.error("Leaving Hermachy control directory in place: master shutdown unconfirmed")
            return confirmed

        def cleanup(self):
            self._shutdown_master()

    _WORKSTATION_CLASS = WorkstationSSHEnvironment
    return WorkstationSSHEnvironment


def is_workstation_environment(env: Any, route: Optional[Route] = None) -> bool:
    cls = _WORKSTATION_CLASS
    if cls is None or not isinstance(env, cls):
        return False
    return route is None or env.hermachy_route == route


def _provider_class():
    from agent.terminal_env_provider import TerminalEnvironmentProvider

    class HermachyProvider(TerminalEnvironmentProvider):
        name = BACKEND
        display_name = "Hermachy workstation"
        is_remote = True
        is_container = False  # keeps dangerous-command approvals on
        supports_streams = False

        @property
        def skip_container_guards(self) -> bool:
            return False

        def is_available(self) -> bool:
            try:
                load_executor()
                return True
            except Exception:
                return False

        def env_description(self) -> str:
            return "the user's Omarchy Linux workstation, through Hermachy"

        def create_environment(self, cwd=None, timeout=None, task_id=None, **_kwargs):
            route = _ROUTE.get()
            if route is None:
                raise RuntimeError("hermachy_ssh runs only inside a Hermachy workstation session")
            return workstation_environment_class()(route, int(timeout or 60))

    return HermachyProvider


# ---------------------------------------------------------------- routing middleware

def _error(message: str) -> str:
    return json.dumps({"status": "error", "error": message, "exit_code": -1, "output": "", "target": "omarchy"})


def _current_context() -> tuple[str, str]:
    from gateway.session_context import get_session_env
    from agent.runtime_cwd import scoped_session_cwd
    return get_session_env("HERMES_SESSION_KEY", ""), scoped_session_cwd()


def _uses_environment(tool_name: str) -> bool:
    if tool_name in ENVIRONMENT_TOOLS:
        return True
    try:
        from tools.registry import registry
        entry = registry.get_entry(tool_name)
        return getattr(entry, "toolset", None) in ENVIRONMENT_TOOLSETS
    except Exception:
        return False


def _evict(task_id: str, session_key: str = "") -> bool:
    """Drop a session's cached environment like ``drop_session_terminal``: the environment under
    its resolved key and raw task id, the recorded cwd, the turn workspace and file-op cache."""
    from tools.terminal_tool import clear_session_cwd
    from tools.terminal_tool_lifecycle import _clear_file_ops_cache, _evict_environment_for_task, get_active_env
    for key in {k for k in (session_key, task_id) if k}:
        try:
            from gateway.session_policy import unbind_turn_workspace
            unbind_turn_workspace(key)
        except Exception:
            pass
        clear_session_cwd(key)
    _evict_environment_for_task(task_id)
    _clear_file_ops_cache(task_id)
    return get_active_env(task_id) is None


def _guard_unbound(task_id: str, session_key: str = "") -> Optional[str]:
    """An unbound session must never reuse an environment built for the workstation."""
    try:
        from tools.terminal_tool_lifecycle import get_active_env
        env = get_active_env(task_id)
    except Exception:
        return None
    if env is not None and is_workstation_environment(env):
        try:
            if _evict(task_id, session_key):
                return None
        except Exception:
            pass
        return "Hermachy could not release a workstation environment from this session. Nothing was executed."
    return None


def route_tool_call(tool_name: str, args: dict, next_call: Callable[[Any], Any], task_id: str = "",
                    session_id: str = "", **_context: Any) -> Any:
    """``tool_execution`` middleware: bind the workstation scope for bound sessions."""
    try:
        session_key, session_cwd = _current_context()
        route = resolve_route(session_id, session_key, session_cwd)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, RouteError) else type(exc).__name__
        return _error(f"Hermachy routing unavailable: {reason}. Nothing was executed.")
    if route is None:
        problem = _guard_unbound(task_id, session_key)
        return _error(problem) if problem else next_call(args)

    if tool_name == "terminal" and isinstance(args, dict) and "target" in args:
        # Chats started under Hermachy 0.5.x keep a prompt that routes with terminal(target=...).
        # Honour it exactly: "hermes" runs on the host, "omarchy" on the workstation.
        target = args.get("target")
        args = {key: value for key, value in args.items() if key != "target"}
        if target == "hermes":
            if any(args.get(key) for key in _FOREGROUND_ONLY):
                return json.dumps({"target": "hermes", "status": "error", "exit_code": -1, "output": "",
                                   "error": "Hermes target supports foreground commands only, up to 300 seconds"})
            host_args = {key: args[key] for key in ("command", "timeout", "workdir") if key in args}
            host_token = _ROUTE.set(route)
            try:
                return run_on_host(host_args)
            finally:
                _ROUTE.reset(host_token)
        if target != "omarchy":
            return _error("target must be omarchy or hermes. Nothing was executed.")

    route_token = _ROUTE.set(route)
    if tool_name == HOST_TOOL:
        try:
            return next_call(args)
        finally:
            _ROUTE.reset(route_token)
    try:
        from tools.terminal_scope import default_terminal_scope, get_terminal_scope, reset_terminal_scope, set_terminal_scope
        scope = dict(get_terminal_scope() or default_terminal_scope())
        scope.update({"TERMINAL_ENV": BACKEND, "TERMINAL_CWD": route.cwd})
        scope_token = set_terminal_scope(scope)
    except Exception as exc:
        _ROUTE.reset(route_token)
        return _error(f"Hermachy routing unavailable ({type(exc).__name__}). Nothing was executed.")
    try:
        try:
            from tools.terminal_tool_lifecycle import ensure_task_env, get_active_env
            env = get_active_env(task_id)
            if env is not None and not is_workstation_environment(env, route):
                # Cached before the binding, or for an older route: never reuse it.
                if not _evict(task_id, session_key):
                    return _error("Hermachy could not replace this session's environment. Nothing was executed.")
                env = None
            if env is None and _uses_environment(tool_name):
                env = ensure_task_env(task_id)
                if not is_workstation_environment(env, route):
                    return _error("Hermachy could not open the workstation environment. Nothing was executed.")
        except Exception as exc:
            return _error(f"Hermachy could not open the workstation environment ({type(exc).__name__}). "
                          "Nothing was executed.")
        return next_call(args)
    finally:
        reset_terminal_scope(scope_token)
        _ROUTE.reset(route_token)


# ---------------------------------------------------------------- explicit host execution

HOST_SCHEMA = {
    "name": HOST_TOOL,
    "description": (
        "Hermachy: run a foreground shell command on the Hermes host (this machine), not on the "
        "user's Omarchy workstation. Only available in Hermachy workstation chats, where the "
        "terminal, file and code tools run on the workstation. Use it for host files, services and "
        "installed CLI tools. Never retry a failed command on the other machine. Supports commands "
        "up to 300 seconds; workdir must be an absolute host path (default: the host user's home)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run on the Hermes host"},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 300, "default": 180},
            "workdir": {"type": "string", "description": "Absolute working directory on the Hermes host"},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
}


def run_on_host(args: dict, **_kwargs: Any) -> str:
    if _ROUTE.get() is None:
        return json.dumps({"status": "error", "target": "hermes", "error":
                           "hermes_host is only available in Hermachy workstation chats; use terminal here"})
    command, timeout, workdir = args.get("command"), args.get("timeout", 180), args.get("workdir")
    if not isinstance(command, str) or not command.strip() or "\x00" in command:
        return json.dumps({"status": "error", "target": "hermes", "error": "command must be a non-empty string"})
    if type(timeout) is not int or not 1 <= timeout <= 300:
        return json.dumps({"status": "error", "target": "hermes", "error": "timeout must be 1-300 seconds"})
    if workdir is not None and (not isinstance(workdir, str) or not workdir.startswith("/") or _has_control(workdir)):
        return json.dumps({"status": "error", "target": "hermes", "error": "workdir must be an absolute host path"})
    directory = workdir or str(Path.home())
    if not Path(directory).is_dir():
        return json.dumps({"status": "error", "target": "hermes", "error": "working directory does not exist on the host"})
    from tools import terminal_tool as terminal
    decision = terminal._check_all_guards(command, "local")
    if not decision.get("approved"):
        return json.dumps(dict(decision, target="hermes", output="", exit_code=-1,
                               error=decision.get("message") or "Command requires approval",
                               status=decision.get("status", "blocked")), ensure_ascii=False)
    from tools.environments.local import LocalEnvironment
    environment = LocalEnvironment(cwd=directory, timeout=timeout, persistent=False)
    try:
        result = environment.execute(command, timeout=timeout)
    finally:
        environment.cleanup()
    result["exit_code"] = result.pop("returncode", -1)
    result.update(target="hermes", workdir=directory)
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------- bound-session instructions

INSTRUCTIONS = """## Hermachy workstation chat
This chat is bound to the user's Omarchy Linux workstation. The terminal, process, file
(read_file, write_file, patch, search_files) and execute_code tools run on the workstation,
starting in `{cwd}`; paths name workstation files. Use `hermes_host` (or, as in older
Hermachy chats, `terminal` with `target: "hermes"`) to run a command on the Hermes host instead. Never retry a failed command on the other machine. A result marked
`hermachy_outcome_unknown` may or may not have run: inspect the workstation before retrying.
Desktop access is supplied automatically; discover desktop commands with `omarchy commands`.
For root operations use `hermachy-exec --admin COMMAND [ARG ...]` (for shell syntax:
`hermachy-exec --admin /bin/bash -c <quoted script>`); authentication appears on the workstation
desktop. Never ask for passwords in chat or pipe them into sudo, and do not retry a cancelled
authentication."""

_SECTION_LIMIT = 4000


def _workspace_instructions(route: Route, budget: int) -> str:
    env = None
    try:
        env = workstation_environment_class()(route, 20)
        result = env.execute("hermachy-exec --context-files " + shlex.quote(route.cwd), timeout=15)
        files = json.loads(result.get("output") or "[]") if result.get("returncode") == 0 else None
    except Exception:
        files = None
    finally:
        if env is not None:
            try:
                env.cleanup()
            except Exception:
                pass
    if not isinstance(files, list):
        return "\n\nProject instructions could not be read from the workstation; read them there if needed."
    parts = []
    for item in files:
        if isinstance(item, dict) and item.get("status") == "read" and isinstance(item.get("content"), str):
            parts.append(f"\n\n### {item.get('label') or item.get('path')}\n{item['content']}")
    text = "".join(parts)
    if not text:
        return ""
    if len(text) > budget:
        text = text[: max(0, budget - 120)] + "\n\n[Truncated; read the full project instructions on the workstation.]"
    return "\n\n## Workstation project instructions" + text


def prompt_section(info: dict) -> str:
    try:
        route = resolve_route(str(info.get("session_id") or ""), "", str(info.get("cwd") or ""))
    except Exception:
        return "## Hermachy\nThis chat's workstation route is unavailable; tool calls will be refused."
    if route is None:
        return ""
    head = INSTRUCTIONS.format(cwd=route.cwd)
    return head + _workspace_instructions(route, _SECTION_LIMIT - len(head) - 40)


# ---------------------------------------------------------------- registration

def register(ctx) -> None:
    ctx.register_terminal_environment_provider(_provider_class()())
    ctx.register_middleware("tool_execution", route_tool_call)
    ctx.register_tool(name=HOST_TOOL, toolset=TOOLSET, schema=HOST_SCHEMA, handler=run_on_host,
                      description=HOST_SCHEMA["description"], emoji="🖥️")
    ctx.register_system_prompt_section("hermachy.workstation", prompt_section, max_chars=_SECTION_LIMIT)
