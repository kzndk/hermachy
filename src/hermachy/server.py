"""Mac Studio forced-command launcher for Hermachy."""

from __future__ import annotations

import base64
import getpass
import json
import os
from dataclasses import dataclass
from pathlib import Path
import posixpath
import shlex
import socket
import stat
import subprocess
import sys
from typing import Any, Mapping, Sequence

from . import __version__
from .protocol import LaunchRequest, ProtocolError, parse_forced_command

DEFAULT_CONFIG_PATH = Path("~/.config/hermachy/server.json")
SAFE_EXECUTOR_CAPABILITY_VERSION = "2"
_HERMES_ENTRYPOINT = "from hermes_cli.main import main; raise SystemExit(main())"
_REQUIRED_CONFIG_FIELDS = frozenset(
    {
        "hermes_command",
        "hermes_source_root",
        "executor_host",
        "executor_user",
        "executor_identity_file",
    }
)
_ALLOWED_CONFIG_FIELDS = _REQUIRED_CONFIG_FIELDS | {"executor_port"}

_EXECUTOR_PROBE = """import base64,getpass,json,os,platform,socket,sys
raw=base64.urlsafe_b64decode(sys.argv[1]+\"=\"*(-len(sys.argv[1])%4))
cwd=raw.decode(\"utf-8\")
os.chdir(cwd)
print(json.dumps({\"hostname\":socket.gethostname(),\"user\":getpass.getuser(),\"cwd\":os.getcwd(),\"system\":platform.system()},sort_keys=True))
"""


class ServerError(ValueError):
    """Raised when the server launch environment is invalid."""


@dataclass(frozen=True)
class ServerConfig:
    hermes_command: Path
    executor_host: str
    executor_user: str
    executor_identity_file: Path
    executor_port: int = 22
    hermes_source_root: Path = Path("/opt/hermes-agent")


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
    """Load a strict, user-owned, mode-private Studio configuration."""

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
        raise ServerError(f"server config has unknown fields: {', '.join(sorted(unknown))}")

    hermes = _absolute_path(payload["hermes_command"], "hermes_command")
    hermes_source_root = _absolute_path(
        payload["hermes_source_root"], "hermes_source_root"
    )
    identity = _absolute_path(
        payload["executor_identity_file"], "executor_identity_file"
    )
    host = _safe_config_text(payload["executor_host"], "executor_host")
    user = _safe_config_text(payload["executor_user"], "executor_user")
    port = payload.get("executor_port", 22)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ServerError("executor_port must be an integer between 1 and 65535")

    if not hermes_source_root.is_dir():
        raise ServerError(f"Hermes source root is missing: {hermes_source_root}")
    runtime_hermes = hermes_source_root / "venv" / "bin" / "hermes"
    if not runtime_hermes.is_file() or not os.access(runtime_hermes, os.X_OK):
        raise ServerError(
            f"Hermes runtime command is missing or not executable: {runtime_hermes}"
        )
    if not hermes.is_file() or not os.access(hermes, os.X_OK):
        raise ServerError(f"Hermes command is missing or not executable: {hermes}")
    try:
        same_runtime = os.path.samefile(hermes, runtime_hermes)
    except OSError as exc:
        raise ServerError(f"cannot verify Hermes runtime command: {exc}") from exc
    if not same_runtime:
        raise ServerError(
            "Hermes command must belong to the same Hermes runtime used for "
            "safe-executor capability verification"
        )
    _require_private_file(identity, "executor SSH identity")

    return ServerConfig(hermes, host, user, identity, port, hermes_source_root)


def _runtime_python_env(
    config: ServerConfig, base_env: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Pin Python imports to the same source tree as the selected venv."""

    env = dict(os.environ if base_env is None else base_env)
    env.pop("PYTHONHOME", None)
    env["PYTHONPATH"] = str(config.hermes_source_root)
    return env


def build_hermes_env(
    config: ServerConfig,
    request: LaunchRequest,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Create child-only settings that route Hermes tools to Omarchy."""

    env = _runtime_python_env(config, base_env)
    env.update(request.env or {})
    env.pop("SSH_ORIGINAL_COMMAND", None)
    env.pop("HERMACHY_SERVER_CONFIG", None)
    env.update(
        {
            "HERMES_TUI": "1",
            # Pin imports to the same source tree whose venv is used for the
            # capability probe and launch. Never inherit an ambient path.
            "PYTHONPATH": str(config.hermes_source_root),
            "TERMINAL_ENV": "ssh",
            "TERMINAL_CWD": request.cwd,
            "TERMINAL_SSH_HOST": config.executor_host,
            "TERMINAL_SSH_USER": config.executor_user,
            "TERMINAL_SSH_PORT": str(config.executor_port),
            "TERMINAL_SSH_KEY": str(config.executor_identity_file),
            "TERMINAL_SSH_PERSISTENT": "true",
            # Stock Hermes currently rewrites terminal env from persistent
            # config, syncs state into SSH backends, and can use ambient keys.
            # Hermachy requires the comprehensive safe-executor capability.
            "TERMINAL_SYNC_REMOTE_STATE": "false",
            "HERMACHY_REQUIRE_SAFE_EXECUTOR": "1",
        }
    )
    return env


def verify_safe_executor_capability(config: ServerConfig) -> None:
    """Fail closed unless Hermes supports Hermachy's complete execution boundary."""

    python = _runtime_python(config)
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ServerError(f"Hermes runtime Python is missing: {python}")
    probe = (
        "from tools.environments.ssh import HERMACHY_CONTROL_VERSION;"
        "print(HERMACHY_CONTROL_VERSION)"
    )
    try:
        completed = subprocess.run(
            [str(python), "-c", probe],
            cwd=config.hermes_source_root,
            env=_runtime_python_env(config),
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ServerError(f"cannot verify Hermes safe-executor capability: {exc}") from exc
    if (
        completed.returncode != 0
        or completed.stdout.strip() != SAFE_EXECUTOR_CAPABILITY_VERSION
    ):
        raise ServerError(
            f"installed Hermes must support Hermachy safe-executor capability v{SAFE_EXECUTOR_CAPABILITY_VERSION}; "
            "refusing an unsafe or misrouted execution session"
        )


def build_hermes_argv(config: ServerConfig, request: LaunchRequest) -> list[str]:
    """Build the direct, non-shell Hermes TUI invocation."""

    argv = [
        str(_runtime_python(config)),
        "-c",
        _HERMES_ENTRYPOINT,
        "--profile=default",
        "--tui",
        "--no-restore-cwd",
    ]
    if request.action == "continue":
        argv.append(
            f"--continue={request.session}" if request.session else "--continue"
        )
    elif request.action == "resume":
        if request.session is None:
            raise ServerError("resume request is missing a session")
        argv.append(f"--resume={request.session}")
    elif request.action not in {"new", "doctor"}:
        raise ServerError(f"cannot launch action: {request.action}")
    return argv


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
    """Verify the authoritative Studio process and outbound Omarchy execution leg."""

    try:
        hermes_check = subprocess.run(
            [str(_runtime_python(config)), "-c", _HERMES_ENTRYPOINT, "--version"],
            cwd=config.hermes_source_root,
            env=_runtime_python_env(config),
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if hermes_check.returncode != 0:
            raise ServerError(
                f"Hermes version check failed with exit status {hermes_check.returncode}"
            )

        executor_check = subprocess.run(
            build_executor_ssh_argv(config, request.cwd),
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if executor_check.returncode != 0:
            detail = executor_check.stderr.strip() or "no SSH error text"
            raise ServerError(
                f"Omarchy execution check failed with exit status "
                f"{executor_check.returncode}: {detail}"
            )
        executor = json.loads(executor_check.stdout)
        _validate_executor_report(config, request, executor)

        report = {
            "status": "ok",
            "product": "Hermachy",
            "version": __version__,
            "studio": {
                "hostname": socket.gethostname(),
                "user": getpass.getuser(),
                "profile": "default",
                "hermes": hermes_check.stdout.strip(),
            },
            "executor": executor,
        }
        print(json.dumps(report, sort_keys=True))
        return 0
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ServerError) as exc:
        print(
            json.dumps(
                {"status": "error", "product": "Hermachy", "error": str(exc)},
                sort_keys=True,
            )
        )
        return 1


def _runtime_python(config: ServerConfig) -> Path:
    """Return the only Python runtime allowed to probe and launch Hermes."""

    return config.hermes_source_root / "venv" / "bin" / "python"


def _validate_executor_report(
    config: ServerConfig, request: LaunchRequest, executor: Any
) -> None:
    if not isinstance(executor, dict):
        raise ServerError("Omarchy identity probe returned a non-object response")
    required = {"hostname", "user", "cwd", "system"}
    if set(executor) != required:
        raise ServerError("Omarchy identity probe returned unexpected fields")
    if any(not isinstance(executor[field], str) or not executor[field] for field in required):
        raise ServerError("Omarchy identity probe returned invalid field values")
    if executor["user"] != config.executor_user:
        raise ServerError("Omarchy identity probe returned the wrong user")
    if executor["system"] != "Linux":
        raise ServerError("executor is not Linux/Omarchy")
    if posixpath.normpath(executor["cwd"]) != posixpath.normpath(request.cwd):
        raise ServerError("Omarchy identity probe returned the wrong working directory")


def resolve_remote_session(config: ServerConfig, request: LaunchRequest) -> int:
    """Resolve native session titles/IDs and workspace-scoped continuation."""
    script = r"""
import json, sys
from hermes_state import SessionDB
cwd, selector = sys.argv[1:]
db = SessionDB()
try:
    sid = None
    if selector and selector.lower() != "latest":
        row = db.get_session(selector)
        sid = row["id"] if row else db.resolve_session_by_title(selector)
    else:
        # Search both surfaces in this workspace before falling back elsewhere.
        for workspace in (cwd, None):
            for source in ("tui", "cli"):
                scope = {"workspace_key": workspace} if workspace is not None else {}
                rows = db.search_sessions(source=source, limit=1, **scope)
                if rows:
                    sid = rows[0]["id"]
                    break
            if sid:
                break
    if not sid:
        print("hermachy: no matching remote session", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({"session": db.get_compression_tip(sid) or sid}))
finally:
    db.close()
"""
    return subprocess.run(
        [str(_runtime_python(config)), "-c", script, request.cwd, request.session or ""],
        cwd=config.hermes_source_root, env=_runtime_python_env(config), check=False,
        timeout=20,
    ).returncode


def has_interactive_tty() -> bool:
    """Return whether SSH allocated the PTY required by the real Hermes TUI."""

    return sys.stdin.isatty() and sys.stdout.isatty()


def main(argv: Sequence[str] | None = None) -> int:
    """Validate a forced request and either diagnose or exec the real Hermes TUI."""

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
        verify_safe_executor_capability(config)
        if request.action == "doctor":
            return run_doctor(config, request)
        if request.action == "resolve":
            return resolve_remote_session(config, request)
        if request.action == "gateway":
            # No remote renderer or PTY: stdout is exclusively native JSON-RPC.
            command = [str(_runtime_python(config)), "-m", "tui_gateway.entry"]
            os.chdir(config.hermes_source_root)
            os.execvpe(command[0], command, build_hermes_env(config, request))
            raise ServerError("Hermes gateway unexpectedly returned")
        if not has_interactive_tty():
            raise ServerError("interactive Hermachy sessions require an SSH PTY")
        command = build_hermes_argv(config, request)
        os.execvpe(command[0], command, build_hermes_env(config, request))
        raise ServerError("Hermes process unexpectedly returned")
    except (ProtocolError, ServerError, OSError, subprocess.SubprocessError) as exc:
        print(f"hermachy-server: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
