"""Omarchy-side Hermes Link client."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence

from . import __version__
from .protocol import LaunchRequest, ProtocolError, encode_request

DEFAULT_CONFIG_PATH = Path("~/.config/hermes-link/client.json")
_REQUIRED_CONFIG_FIELDS = frozenset({"studio_host", "studio_user", "identity_file"})
_ALLOWED_CONFIG_FIELDS = _REQUIRED_CONFIG_FIELDS | {"studio_port"}


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
    configured = os.environ.get("HLINK_CLIENT_CONFIG")
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
        "-tt",
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
        f"hlink-v1 {encode_request(request)}",
    ]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="hlink",
        description="Hermes Link — Morpheus knowledge with local Omarchy execution",
    )
    action = result.add_mutually_exclusive_group()
    action.add_argument(
        "-c",
        "--continue",
        dest="continue_session",
        nargs="?",
        const="",
        metavar="NAME",
        help="continue the latest session, or a named session",
    )
    action.add_argument(
        "-r",
        "--resume",
        metavar="SESSION",
        help="resume a specific Hermes session ID or title",
    )
    action.add_argument(
        "--doctor",
        action="store_true",
        help="verify Studio and Omarchy execution connectivity",
    )
    result.add_argument(
        "--cwd",
        metavar="DIR",
        help="Omarchy working directory (default: current directory)",
    )
    result.add_argument("--version", action="version", version=f"Hermes Link {__version__}")
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return parser().parse_args(argv)


def build_request(args: argparse.Namespace, current_cwd: Path) -> LaunchRequest:
    """Turn parsed local CLI options into a validated launch request."""

    if args.cwd:
        cwd_path = Path(os.path.abspath(os.path.expanduser(args.cwd)))
        if not cwd_path.is_dir():
            raise ClientError(f"working directory does not exist or is not a directory: {cwd_path}")
    else:
        cwd_path = current_cwd if current_cwd.is_absolute() else Path(os.path.abspath(current_cwd))

    if args.doctor:
        return LaunchRequest(str(cwd_path), "doctor")
    if args.resume is not None:
        return LaunchRequest(str(cwd_path), "resume", args.resume)
    if args.continue_session is not None:
        session = args.continue_session or None
        return LaunchRequest(str(cwd_path), "continue", session)
    return LaunchRequest(str(cwd_path), "new")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the linked TUI and return OpenSSH's exit status."""

    try:
        args = parse_args(argv)
        config = load_client_config(_config_path())
        if not config.identity_file.is_file():
            raise ClientError(f"SSH identity not found: {config.identity_file}")
        request = build_request(args, Path.cwd())
        completed = subprocess.run(build_ssh_argv(config, request), check=False)
        return completed.returncode
    except (ClientError, ProtocolError, OSError) as exc:
        print(f"hlink: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
