"""Versioned, shell-safe launch protocol for Hermachy."""

from __future__ import annotations

import base64
import binascii
import json
import re
import shlex
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1
MAX_TOKEN_BYTES = 8192
MAX_CWD_CHARS = 4096
MAX_SESSION_CHARS = 512
ACTIONS = frozenset({"new", "continue", "resume", "doctor", "gateway", "resolve"})
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_REQUIRED_FIELDS = frozenset({"v", "cwd", "action"})
_ALLOWED_FIELDS = _REQUIRED_FIELDS | {"session", "env"}


class ProtocolError(ValueError):
    """Raised when a Hermachy launch request is invalid."""


@dataclass(frozen=True)
class LaunchRequest:
    """A validated request to launch or inspect a linked Hermes session."""

    cwd: str
    action: str = "new"
    session: str | None = None
    env: dict[str, str] | None = None


# Only presentation/invocation settings; execution routing and Python imports
# remain entirely under the server launcher's control.
LAUNCH_ENV_KEYS = frozenset({
    "HERMES_MODEL", "HERMES_INFERENCE_MODEL", "HERMES_TUI_PROVIDER",
    "HERMES_INFERENCE_PROVIDER", "HERMES_TUI_TOOLSETS", "HERMES_TUI_SKILLS",
    "HERMES_TUI_CHECKPOINTS", "HERMES_TUI_PASS_SESSION_ID", "HERMES_TUI_MAX_TURNS",
    "HERMES_TUI_TOOL_PROGRESS", "HERMES_ACCEPT_HOOKS", "HERMES_YOLO_MODE",
    "HERMES_IGNORE_USER_CONFIG", "HERMES_IGNORE_RULES", "HERMES_SAFE_MODE",
})


def _contains_control(text: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in text)


def _validate_text(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"{field} must be a non-empty string")
    if len(value) > maximum:
        raise ProtocolError(f"{field} is too long")
    if _contains_control(value):
        raise ProtocolError(f"{field} contains control characters")
    return value


def _validate_payload(payload: Any) -> LaunchRequest:
    if not isinstance(payload, dict):
        raise ProtocolError("request must be a JSON object")

    keys = frozenset(payload)
    missing = _REQUIRED_FIELDS - keys
    unknown = keys - _ALLOWED_FIELDS
    if missing:
        raise ProtocolError(f"request is missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ProtocolError(f"request contains unknown fields: {', '.join(sorted(unknown))}")

    version = payload["v"]
    if type(version) is not int or version != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported protocol version: {version!r}")

    cwd = _validate_text(payload["cwd"], "cwd", maximum=MAX_CWD_CHARS)
    if not cwd.startswith("/"):
        raise ProtocolError("cwd must be an absolute POSIX path")

    action = payload["action"]
    if not isinstance(action, str) or action not in ACTIONS:
        raise ProtocolError(f"unsupported action: {action!r}")

    has_session = "session" in payload
    session: str | None = None
    if has_session:
        session = _validate_text(
            payload["session"], "session", maximum=MAX_SESSION_CHARS
        )
        if session.startswith("-"):
            raise ProtocolError("session must not begin with '-'")

    if action in {"new", "doctor"} and has_session:
        raise ProtocolError(f"action {action!r} does not accept a session")
    if action == "resume" and not has_session:
        raise ProtocolError("resume requires a session")

    launch_env = payload.get("env")
    if launch_env is not None:
        if not isinstance(launch_env, dict) or set(launch_env) - LAUNCH_ENV_KEYS:
            raise ProtocolError("unsupported launch environment")
        if action != "gateway":
            raise ProtocolError("launch environment is only valid for gateway")
        launch_env = {k: _validate_text(v, k, maximum=2048)
                      for k, v in launch_env.items()}
    if action == "gateway" and has_session:
        raise ProtocolError("gateway does not accept a session")
    return LaunchRequest(cwd=cwd, action=action, session=session, env=launch_env)


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def encode_request(request: LaunchRequest) -> str:
    """Validate and encode a request as unpadded URL-safe base64 JSON."""

    payload: dict[str, Any] = {
        "v": PROTOCOL_VERSION,
        "cwd": request.cwd,
        "action": request.action,
    }
    if request.session is not None:
        payload["session"] = request.session
    if request.env is not None:
        payload["env"] = request.env
    validated = _validate_payload(payload)

    serializable: dict[str, Any] = {
        "action": validated.action,
        "cwd": validated.cwd,
        "v": PROTOCOL_VERSION,
    }
    if validated.session is not None:
        serializable["session"] = validated.session

    if validated.env is not None:
        serializable["env"] = validated.env

    raw = json.dumps(
        serializable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    if len(token) > MAX_TOKEN_BYTES:
        raise ProtocolError("encoded request is too large")
    return token


def decode_request(token: str) -> LaunchRequest:
    """Decode and validate an unpadded URL-safe base64 JSON request."""

    if not isinstance(token, str) or not token:
        raise ProtocolError("request token must be a non-empty string")
    if len(token) > MAX_TOKEN_BYTES:
        raise ProtocolError("encoded request is too large")
    if not _TOKEN_RE.fullmatch(token):
        raise ProtocolError("request token is not URL-safe base64")

    try:
        raw = base64.b64decode(
            token + "=" * (-len(token) % 4), altchars=b"-_", validate=True
        )
        payload = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_object_without_duplicates
        )
    except ProtocolError:
        raise
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        OverflowError,
    ) as exc:
        raise ProtocolError("request token is not valid encoded JSON") from exc

    return _validate_payload(payload)


def parse_forced_command(command: str | None) -> LaunchRequest:
    """Parse the exact command accepted by the restricted SSH key."""

    try:
        words = shlex.split(command or "", posix=True)
    except ValueError as exc:
        raise ProtocolError("malformed forced command") from exc
    if len(words) != 2 or words[0] != "hermachy-v1":
        raise ProtocolError("expected: hermachy-v1 <request>")
    return decode_request(words[1])
