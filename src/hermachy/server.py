"""Hermes-host forced-command endpoint for Hermachy (attach, resolve, doctor)."""

from __future__ import annotations

import base64
import getpass
import json
import os
from dataclasses import dataclass
from pathlib import Path
import posixpath
import re
import shlex
import socket
import stat
import subprocess
import sys
from typing import Any, Sequence

from . import __version__
from .protocol import LaunchRequest, ProtocolError, parse_forced_command

DEFAULT_CONFIG_PATH = Path("~/.config/hermachy/server.json")
_REQUIRED_CONFIG_FIELDS = frozenset(
    {"hermes_command", "executor_host", "executor_user", "executor_identity_file"}
)
_ALLOWED_CONFIG_FIELDS = _REQUIRED_CONFIG_FIELDS | {"executor_port", "hermes_home"}
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")
PLUGIN_NAME = "hermachy"

_EXECUTOR_PROBE = """import base64,getpass,json,os,platform,socket,subprocess,sys
raw=base64.urlsafe_b64decode(sys.argv[1]+\"=\"*(-len(sys.argv[1])%4))
cwd=raw.decode(\"utf-8\")
os.chdir(cwd)
try:
    check=subprocess.run([\"hermachy-exec\",\"--doctor\"],stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=12)
    access=json.loads(check.stdout) if check.returncode == 0 else {\"error\":\"workstation entry point failed\"}
except (OSError,ValueError,subprocess.SubprocessError):
    access={\"error\":\"install the Hermachy workstation entry point\"}
print(json.dumps({\"hostname\":socket.gethostname(),\"user\":getpass.getuser(),\"cwd\":os.getcwd(),\"system\":platform.system(),\"access\":access},sort_keys=True))
"""


class ServerError(ValueError):
    """Raised when the server configuration or request is invalid."""


@dataclass(frozen=True)
class ServerConfig:
    hermes_command: Path
    executor_host: str
    executor_user: str
    executor_identity_file: Path
    executor_port: int = 22
    hermes_home: Path = Path("~/.hermes").expanduser()


def _safe_config_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ServerError(f"{field} must be a non-empty string")
    if any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise ServerError(f"{field} contains whitespace or control characters")
    return value


def _absolute_path(value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ServerError(f"{field} must be a non-empty string")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ServerError(f"{field} contains control characters")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ServerError(f"{field} must resolve to an absolute path")
    return path


def _server_config_path() -> Path:
    configured = os.environ.get("HERMACHY_SERVER_CONFIG")
    return Path(configured).expanduser() if configured else DEFAULT_CONFIG_PATH.expanduser()


def _require_private_file(path: Path, label: str) -> os.stat_result:
    try:
        info = path.stat()
    except OSError as exc:
        raise ServerError(f"{label} not found: {path}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ServerError(f"{label} is not a regular file: {path}")
    if info.st_uid != os.getuid():
        raise ServerError(f"{label} is not owned by the current user: {path}")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ServerError(f"{label} permissions must not allow group or other access: {path}")
    return info


def load_server_config(path: Path) -> ServerConfig:
    """Load a strict, user-owned, mode-private host configuration."""

    _require_private_file(path, "server config")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServerError(f"cannot read server config {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ServerError("server config must be a JSON object")
    keys = frozenset(payload)
    missing = _REQUIRED_CONFIG_FIELDS - keys
    unknown = keys - _ALLOWED_CONFIG_FIELDS
    if missing:
        raise ServerError(f"server config is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise ServerError(f"server config has unknown fields: {', '.join(sorted(unknown))}; "
                          "re-run hermachy-admin studio-finish for 0.6")

    hermes = _absolute_path(payload["hermes_command"], "hermes_command")
    if not hermes.is_file() or not os.access(hermes, os.X_OK):
        raise ServerError(f"Hermes command is missing or not executable: {hermes}")
    identity = _absolute_path(payload["executor_identity_file"], "executor_identity_file")
    _require_private_file(identity, "executor SSH identity")
    host = _safe_config_text(payload["executor_host"], "executor_host")
    user = _safe_config_text(payload["executor_user"], "executor_user")
    port = payload.get("executor_port", 22)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ServerError("executor_port must be an integer between 1 and 65535")
    home = _absolute_path(payload.get("hermes_home", "~/.hermes"), "hermes_home")
    if not home.is_dir():
        raise ServerError(f"Hermes home is missing: {home}")
    return ServerConfig(hermes, host, user, identity, port, home)


def _hermes_env(config: ServerConfig) -> dict[str, str]:
    from .bridge import _hermes_env as clean
    return clean(config.hermes_home)


def hermes_version(config: ServerConfig) -> str:
    done = subprocess.run([str(config.hermes_command), "--version"], env=_hermes_env(config), cwd=str(Path.home()),
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False)
    if done.returncode != 0:
        raise ServerError(f"Hermes version check failed with exit status {done.returncode}")
    return done.stdout.strip()


def installed_plugin_version(config: ServerConfig) -> str | None:
    manifest = config.hermes_home / "plugins" / PLUGIN_NAME / "plugin.yaml"
    try:
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip().strip('"')
    except OSError:
        return None
    return None


def plugin_enabled(config: ServerConfig) -> bool:
    """Best-effort read of ``plugins.enabled`` from the default profile's config.yaml."""
    try:
        lines = (config.hermes_home / "config.yaml").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    inside_plugins = inside_enabled = False
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            inside_plugins, inside_enabled = line.rstrip() == "plugins:", False
            continue
        if inside_plugins and line.strip().startswith("enabled:"):
            value = line.split(":", 1)[1].strip()
            if value.startswith("["):
                return PLUGIN_NAME in [v.strip().strip("'\"") for v in value.strip("[]").split(",")]
            inside_enabled = True
            continue
        if inside_enabled and line.strip().startswith("- "):
            if line.strip()[2:].strip().strip("'\"") == PLUGIN_NAME:
                return True
        elif inside_enabled and not line.strip().startswith("- "):
            inside_enabled = False
    return False


def build_executor_ssh_argv(config: ServerConfig, cwd: str) -> list[str]:
    """Build the fixed outbound identity probe without embedding cwd as shell text."""

    cwd_token = base64.urlsafe_b64encode(cwd.encode("utf-8")).rstrip(b"=").decode("ascii")
    remote_command = f"python3 -c {shlex.quote(_EXECUTOR_PROBE)} {cwd_token}"
    return [
        "ssh",
        "-T",
        "-F",
        "/dev/null",
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "ForwardAgent=no",
        "-i",
        str(config.executor_identity_file),
        "-p",
        str(config.executor_port),
        f"{config.executor_user}@{config.executor_host}",
        remote_command,
    ]


def run_doctor(config: ServerConfig, request: LaunchRequest) -> int:
    """Check the host's Hermes, its gateway and plugin, and the outbound workstation leg."""
    from .bridge import BridgeError, check_plugin, request_once
    try:
        version = hermes_version(config)
        warnings: list[str] = []
        try:
            loaded = request_once(config.hermes_command, "plugins.list", {}, hermes_home=config.hermes_home)
            gateway = "attachable"
        except (BridgeError, OSError, ValueError) as exc:
            raise ServerError(f"the default profile's Hermes gateway is not attachable: {exc}") from exc
        try:
            check_plugin(loaded)
        except BridgeError as exc:
            raise ServerError(str(exc)) from exc
        plugin = installed_plugin_version(config)
        if plugin != __version__:
            raise ServerError(f"Hermachy plugin {plugin or 'missing'} is installed; expected {__version__}; "
                              "re-run hermachy-admin studio-finish")
        if not plugin_enabled(config):
            raise ServerError("the Hermachy plugin is not listed in plugins.enabled of the default profile")

        executor_check = subprocess.run(build_executor_ssh_argv(config, request.cwd), check=False,
                                        capture_output=True, text=True, timeout=20)
        if executor_check.returncode != 0:
            detail = executor_check.stderr.strip() or "no SSH error text"
            raise ServerError(f"Omarchy execution check failed with exit status "
                              f"{executor_check.returncode}: {detail}")
        executor = json.loads(executor_check.stdout)
        _validate_executor_report(config, request, executor)
        report = {
            "status": "ok",
            "product": "Hermachy",
            "version": __version__,
            "transport": "shared-gateway-v1",
            "execution_targets": ["omarchy", "hermes"],
            "studio": {"hostname": socket.gethostname(), "user": getpass.getuser(), "profile": "default",
                       "hermes": version, "gateway": gateway, "plugin": plugin},
            "executor": executor,
            "warnings": warnings + executor_warnings(executor),
        }
        print(json.dumps(report, sort_keys=True))
        return 0
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ServerError) as exc:
        print(json.dumps({"status": "error", "product": "Hermachy", "version": __version__, "error": str(exc)},
                         sort_keys=True))
        return 1


def _validate_executor_report(
    config: ServerConfig, request: LaunchRequest, executor: Any
) -> None:
    if not isinstance(executor, dict):
        raise ServerError("Omarchy identity probe returned a non-object response")
    required = {"hostname", "user", "cwd", "system"}
    if not required <= set(executor) or set(executor) - required - {"access"}:
        raise ServerError("Omarchy identity probe returned unexpected fields")
    if any(not isinstance(executor[field], str) or not executor[field] for field in required):
        raise ServerError("Omarchy identity probe returned invalid field values")
    if executor["user"] != config.executor_user:
        raise ServerError("Omarchy identity probe returned the wrong user")
    if executor["system"] != "Linux":
        raise ServerError("executor is not Linux/Omarchy")
    if posixpath.normpath(executor["cwd"]) != posixpath.normpath(request.cwd):
        raise ServerError("Omarchy identity probe returned the wrong working directory")


def executor_warnings(executor: dict) -> list[str]:
    access = executor.get("access", {})
    if not isinstance(access, dict) or access.get("version") != "1" or not access.get("ssh_entrypoint"):
        return ["Omarchy desktop access is not installed on the execution SSH key; reinstall the workstation endpoint"]
    warnings = []
    if not access.get("desktop") or not access.get("wayland"):
        warnings.append("Omarchy desktop is unavailable; log into Hyprland for desktop control")
    if not access.get("user_bus"):
        warnings.append("Omarchy user service bus is unavailable")
    if not access.get("administration", {}).get("available"):
        warnings.append("Omarchy graphical administration is unavailable; install polkit")
    return warnings


def resolve_session(config: ServerConfig, request: LaunchRequest) -> int:
    """Map ``--continue`` / ``--resume SELECTOR`` to a session id of the default profile."""
    from .bridge import Bindings, BridgeError, request_once
    selector = (request.session or "").strip()
    try:
        session = None
        if not selector or selector.lower() == "latest":
            session = Bindings(config.hermes_home).latest_session_for(request.cwd)
            if session is None:
                result = request_once(config.hermes_command, "session.most_recent", {}, hermes_home=config.hermes_home)
                session = result.get("session_id") if isinstance(result, dict) else None
        else:
            result = request_once(config.hermes_command, "session.list", {"title": selector, "limit": 5},
                                  hermes_home=config.hermes_home)
            rows = result.get("sessions") if isinstance(result, dict) else result
            for row in rows or []:
                if isinstance(row, dict) and selector in (row.get("id"), row.get("session_id"), row.get("title")):
                    session = row.get("session_id") or row.get("id")
                    break
            if session is None and _SESSION_ID_RE.fullmatch(selector):
                session = selector
    except (BridgeError, OSError, ValueError) as exc:
        print(f"hermachy: could not resolve the session: {exc}", file=sys.stderr)
        return 1
    if not isinstance(session, str) or not session:
        print("hermachy: no matching session", file=sys.stderr)
        return 1
    print(json.dumps({"session": session}))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the forced request and run it."""

    arguments = list(argv if argv is not None else sys.argv[1:])
    if arguments == ["--version"]:
        print(f"Hermachy server {__version__}")
        return 0
    if arguments in (["--help"], ["-h"]):
        print("usage: hermachy-server [--version]")
        return 0
    if arguments:
        print("hermachy-server: command-line arguments are not accepted", file=sys.stderr)
        return 2
    try:
        request = parse_forced_command(os.environ.get("SSH_ORIGINAL_COMMAND"))
        config = load_server_config(_server_config_path())
        if request.action == "doctor":
            return run_doctor(config, request)
        if request.action == "resolve":
            return resolve_session(config, request)
        if request.action == "attach":
            from .bridge import BridgeError, run_attach
            try:
                return run_attach(config.hermes_command, config.hermes_home, request.connection or "",
                                  request.cwd, sys.stdin.buffer, sys.stdout.buffer)
            except BridgeError as exc:
                raise ServerError(str(exc)) from exc
        raise ServerError(f"cannot run action: {request.action}")
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"hermachy-server: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
