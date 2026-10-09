"""Omarchy-side Hermachy client."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import uuid
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence

from . import __version__
from .protocol import LaunchRequest, ProtocolError, encode_request

DEFAULT_CONFIG_PATH = Path("~/.config/hermachy/client.json")
_REQUIRED_CONFIG_FIELDS = frozenset({"studio_host", "studio_user", "identity_file"})
_ALLOWED_CONFIG_FIELDS = _REQUIRED_CONFIG_FIELDS | {"studio_port"}
REMOTE_CHECK_TIMEOUT = 60


class ClientError(ValueError):
    """Raised when local client input or configuration is invalid."""


@dataclass(frozen=True)
class ClientConfig:
    studio_host: str
    studio_user: str
    identity_file: Path
    studio_port: int = 22


def _safe_config_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ClientError(f"{field} must be a non-empty string")
    if any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise ClientError(f"{field} contains whitespace or control characters")
    return value


def _config_path() -> Path:
    configured = os.environ.get("HERMACHY_CLIENT_CONFIG")
    return Path(configured).expanduser() if configured else DEFAULT_CONFIG_PATH.expanduser()


def load_client_config(path: Path) -> ClientConfig:
    """Load and strictly validate the private client JSON configuration."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ClientError(f"client config not found: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClientError(f"cannot read client config {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ClientError("client config must be a JSON object")
    keys = frozenset(payload)
    missing = _REQUIRED_CONFIG_FIELDS - keys
    unknown = keys - _ALLOWED_CONFIG_FIELDS
    if missing:
        raise ClientError(f"client config is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise ClientError(f"client config has unknown fields: {', '.join(sorted(unknown))}")

    host = _safe_config_text(payload["studio_host"], "studio_host")
    user = _safe_config_text(payload["studio_user"], "studio_user")
    identity_text = payload["identity_file"]
    if not isinstance(identity_text, str) or not identity_text:
        raise ClientError("identity_file must be a non-empty string")
    if any(ord(char) < 32 or ord(char) == 127 for char in identity_text):
        raise ClientError("identity_file contains control characters")
    identity = Path(identity_text).expanduser()
    if not identity.is_absolute():
        raise ClientError("identity_file must resolve to an absolute path")

    port = payload.get("studio_port", 22)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ClientError("studio_port must be an integer between 1 and 65535")

    return ClientConfig(host, user, identity, port)


def build_ssh_argv(config: ClientConfig, request: LaunchRequest) -> list[str]:
    """Build a non-shell OpenSSH invocation for the restricted Studio key."""

    return [
        "ssh",
        "-T" if request.action in {"gateway", "resolve", "doctor"} else "-tt",
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
        "-o",
        "PermitLocalCommand=no",
        "-i",
        str(config.identity_file),
        "-p",
        str(config.studio_port),
        f"{config.studio_user}@{config.studio_host}",
        f"hermachy-v1 {encode_request(request)}",
    ]


def parser() -> argparse.ArgumentParser:
    # Vendored verbatim from the same Hermes revision as the native UI bundle.
    from . import hermes_parser
    # Upstream now resolves its help text through hermes_constants. The local
    # zipapp has no Hermes installation; configuration belongs to the host.
    hermes_parser._cfg_path = lambda: "the remote default profile's config.yaml"
    result, _, chat = hermes_parser.build_top_level_parser()
    result.prog = "hermachy"
    result.description = "Local Hermes TUI · persistent connection · Omarchy and Hermes execution"
    result.epilog = "Run hermachy in a project, or hermachy --in DIR. Use /help inside the native TUI."
    for target in (result, chat):
        target.add_argument("--cwd", help="Alias for --in: local working directory",
                            default=argparse.SUPPRESS)
        target.add_argument("--doctor", action="store_true", default=argparse.SUPPRESS,
                            help="Check local UI and the remote agent/executor connection")
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    result = parser()
    args = result.parse_args(argv)
    args.cwd = getattr(args, "cwd", None) or args.in_dir
    args.doctor = getattr(args, "doctor", False)
    args.continue_session = "" if args.continue_last is True else args.continue_last
    if args.continue_last is not None and args.resume is not None:
        result.error("--continue and --resume are mutually exclusive")
    return args


def build_request(args: argparse.Namespace, current_cwd: Path) -> LaunchRequest:
    """Turn parsed local CLI options into a validated launch request."""

    if args.cwd:
        cwd_path = Path(args.cwd).expanduser().resolve()
        if not cwd_path.is_dir():
            raise ClientError(f"working directory does not exist or is not a directory: {cwd_path}")
    else:
        cwd_path = current_cwd.resolve()

    if args.doctor:
        return LaunchRequest(str(cwd_path), "doctor")
    if args.resume is not None:
        return LaunchRequest(str(cwd_path), "resume", args.resume)
    if args.continue_session is not None:
        session = args.continue_session or None
        return LaunchRequest(str(cwd_path), "continue", session)
    return LaunchRequest(str(cwd_path), "new")


def build_launch_env(args: argparse.Namespace) -> dict[str, str]:
    env: dict[str, str] = {}
    for attr, keys in {
        "model": ("HERMES_MODEL", "HERMES_INFERENCE_MODEL"),
        "provider": ("HERMES_TUI_PROVIDER", "HERMES_INFERENCE_PROVIDER"),
        "toolsets": ("HERMES_TUI_TOOLSETS",),
        "max_turns": ("HERMES_TUI_MAX_TURNS",),
    }.items():
        value = getattr(args, attr, None)
        if value is not None:
            env.update({key: str(value) for key in keys})
    if args.skills:
        env["HERMES_TUI_SKILLS"] = ",".join(args.skills)
    for attr, key in {
        "checkpoints": "HERMES_TUI_CHECKPOINTS",
        "pass_session_id": "HERMES_TUI_PASS_SESSION_ID",
        "accept_hooks": "HERMES_ACCEPT_HOOKS",
        "yolo": "HERMES_YOLO_MODE",
        "ignore_user_config": "HERMES_IGNORE_USER_CONFIG",
        "ignore_rules": "HERMES_IGNORE_RULES",
        "safe_mode": "HERMES_SAFE_MODE",
    }.items():
        if getattr(args, attr, False):
            env[key] = "1"
    if args.safe_mode:
        env.update(HERMES_IGNORE_USER_CONFIG="1", HERMES_IGNORE_RULES="1")
    if getattr(args, "verbose", False):
        env["HERMES_TUI_TOOL_PROGRESS"] = "verbose"
    elif getattr(args, "quiet", False):
        env["HERMES_TUI_TOOL_PROGRESS"] = "off"
    return env


def resolve_session(config: ClientConfig, request: LaunchRequest) -> str:
    result = subprocess.run(build_ssh_argv(config, request), capture_output=True,
                            text=True, check=False, timeout=30)
    if result.returncode:
        raise ClientError(result.stderr.strip() or "could not resolve remote session")
    try:
        response = json.loads(result.stdout)
        if not isinstance(response, dict):
            raise ValueError("response must be an object")
        session = response["session"]
        if not isinstance(session, str) or not session:
            raise ValueError("empty session")
        return session
    except (ValueError, KeyError) as exc:
        raise ClientError("invalid remote session response") from exc


def check_remote(config: ClientConfig, cwd: str) -> dict[str, Any]:
    """Check compatibility and the executor before the UI can start retrying."""
    check = subprocess.run(
        build_ssh_argv(config, LaunchRequest(cwd, "doctor")), check=False,
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        timeout=REMOTE_CHECK_TIMEOUT,
    )
    if check.returncode:
        raise ClientError((check.stderr or check.stdout or "remote check failed").strip())
    try:
        report = json.loads(check.stdout)
        if not isinstance(report, dict) or report.get("status") != "ok":
            raise ValueError("diagnostic did not report success")
    except ValueError as exc:
        raise ClientError("invalid remote diagnostic response") from exc
    return report


def ui_paths() -> tuple[Path, Path]:
    root = Path(os.environ.get("HERMACHY_UI_DIR", "~/.local/share/hermachy/tui")).expanduser()
    gateway = Path(os.environ.get("HERMACHY_GATEWAY_COMMAND", "~/.local/bin/hermachy-gateway")).expanduser()
    return root / "entry.mjs", gateway


def prepare_local_ui(args: argparse.Namespace, config: ClientConfig,
                     request: LaunchRequest) -> tuple[list[str], dict[str, str]]:
    entry, gateway = ui_paths()
    node = shutil.which("node")
    if not node or not entry.is_file() or not gateway.is_file():
        raise ClientError("local Hermes UI is missing; install the TUI bundle, Node.js, and hermachy-gateway")
    # Hermachy owns these launch settings. Do not inherit an unrelated Hermes
    # profile, gateway URL, or a stale resume flag from the user's shell.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("HERMES_", "HERMACHY_REQUEST", "HERMACHY_REASONING"))}
    state = Path("~/.local/state/hermachy").expanduser()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    env.update(HERMES_HOME=str(state), HERMES_CWD=request.cwd,
               HERMES_PYTHON=str(gateway), HERMES_PYTHON_SRC_ROOT=str(entry.parent),
               NODE_ENV="production", HERMACHY_REQUEST=encode_request(request),
               HERMACHY_CLIENT_CONFIG=str(_config_path().resolve()))
    reasoning = getattr(args, "reasoning", None)
    if reasoning:
        if reasoning not in {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}:
            raise ClientError("invalid reasoning effort")
        env["HERMACHY_REASONING"] = reasoning
    if args.resume or args.continue_last is not None:
        selector = args.resume or (args.continue_last if isinstance(args.continue_last, str) else None)
        env["HERMES_TUI_RESUME"] = resolve_session(
            config, LaunchRequest(request.cwd, "resolve", selector))
    query = getattr(args, "query", None)
    query_file = getattr(args, "query_file", None)
    if query_file:
        query = sys.stdin.read() if query_file == "-" else Path(query_file).expanduser().read_text()
    if query:
        env["HERMES_TUI_QUERY"] = query
    image = getattr(args, "image", None)
    if image:
        image_path = Path(image).expanduser().resolve(strict=True)
        env["HERMES_TUI_IMAGE"] = str(image_path)
    return [node, "--expose-gc", str(entry)], env


def main(argv: Sequence[str] | None = None) -> int:
    """Render Hermes locally; only the agent JSON-RPC traffic crosses SSH."""
    try:
        args = parse_args(argv)
        if args.version:
            print(f"Hermachy {__version__} · local Hermes TUI / remote agent")
            return 0
        if getattr(args, "output_format", "text") != "text":
            raise ClientError("--format is a single-query CLI option; hermachy runs the native TUI")
        for attr in ("cli", "tui_dev", "oneshot", "oneshot_exit", "usage_file", "run_budget", "source"):
            if getattr(args, attr, None):
                raise ClientError(f"{attr.replace('_', '-')} is a non-TUI/development option; hermachy runs the native TUI")
        if getattr(args, "create_if_missing", False):
            raise ClientError("use /new followed by /title in the TUI to create a named session")
        config = load_client_config(_config_path())
        if not config.identity_file.is_file():
            raise ClientError(f"SSH identity not found: {config.identity_file}")
        request = build_request(args, Path.cwd())
        if args.doctor:
            entry, gateway = ui_paths()
            if not shutil.which("node") or not entry.is_file() or not gateway.is_file():
                raise ClientError("local Hermes TUI runtime is missing")
            report = check_remote(config, request.cwd)
            report["client"] = {"hostname": socket.gethostname(), "system": sys.platform,
                                "renderer": "local native Hermes TUI", "bundle": str(entry)}
            report["transport"] = "SSH JSON-RPC, no remote PTY"
            print(json.dumps(report, sort_keys=True))
            return 0
        cwd = request.cwd
        if args.worktree:
            root = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                                  check=True, capture_output=True, text=True).stdout.strip()
            target = Path(root).parent / (Path(root).name + "-hermachy-" + uuid.uuid4().hex[:8])
            subprocess.run(["git", "-C", root, "worktree", "add", "--detach", str(target)], check=True)
            cwd = str(target)
            print(f"Local worktree: {cwd}", file=sys.stderr)
        request = LaunchRequest(cwd, "gateway", env=build_launch_env(args),
                                connection=uuid.uuid4().hex)
        report = check_remote(config, cwd)
        if report.get("persistent_gateway") != 1 or report.get("execution_targets") != ["omarchy", "hermes"]:
            raise ClientError("remote Hermachy server needs upgrading for persistent connections and dual execution")
        for warning in report.get("warnings", []):
            print(f"hermachy: {warning}", file=sys.stderr)
        command, env = prepare_local_ui(args, config, request)
        return subprocess.run(command, cwd=cwd, env=env, check=False).returncode
    except (ClientError, ProtocolError, OSError, subprocess.SubprocessError) as exc:
        print(f"hermachy: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
