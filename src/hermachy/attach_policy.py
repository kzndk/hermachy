"""What a Hermachy client may send to the shared Hermes gateway, and how it is rewritten.

Every attached WebSocket is fully privileged upstream, so the bridge is the policy point:
only methods the pinned Hermes declares are forwarded, minus methods that would run or read
on the Hermes host, move the session's workspace or profile, or hand secrets to the client.
``session.create`` is rewritten so the session is bound to the workstation and cannot be
created in a bypass mode that skips the Hermachy plugin.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Optional

from .attach_methods import DECLARED_METHODS

# Host execution, host file or clipboard reads, path completion on the wrong machine.
_HOST_ACCESS = frozenset({
    "cli.exec", "shell.exec", "clipboard.paste", "file.attach", "image.attach", "pdf.attach",
    "input.detect_drop", "image.render", "complete.path",
    "session.foreign.import", "session.foreign.list", "session.foreign.preview",
    "projects.add_folder", "projects.remove_folder", "projects.discover_repos",
    "projects.record_repos", "projects.tree", "projects.create",
})
# Secrets that would leave the host.
_SECRETS = frozenset({"vault.preview.fill"})
# The workstation cwd and the default profile are fixed by the launching client.
_ROUTING = frozenset({"session.cwd.set", "session.workspace.move"})
# Owner, worker and relay verbs: never a TUI's business.
_OWNER = frozenset({"cron.submit", "cron.recover", "cron.cancel", "kanban.run", "a2a.forward"})
_DENIED_PREFIXES = ("profile.", "profiles.", "worker.", "bot_relay.")
DENIED_METHODS = _HOST_ACCESS | _SECRETS | _ROUTING | _OWNER

# session.create fields a Hermachy client may not choose.
_BYPASS_FIELDS = ("safe_mode", "ignore_user_config")
# Toolsets stay allowed: routing applies to every tool call of a bound session regardless.
_FORBIDDEN_CREATE_FIELDS = ("cwd_explicit", "row_cwd", "follow_profile_config")
_STRIPPED_CREATE_FIELDS = ("client_env",)
BINDING_METHODS = frozenset({"session.create", "session.resume"})
MAX_ID_TEXT = 200


class PolicyRefusal(ValueError):
    """A client frame that must not reach the gateway."""

    def __init__(self, request_id: Any, message: str):
        super().__init__(message)
        self.request_id = request_id


@dataclass(frozen=True)
class Forward:
    """A frame to forward. ``binding`` names a method whose answer binds its session."""

    frame: str
    request_id: Any = None
    binding: Optional[str] = None


def method_allowed(method: str) -> bool:
    return (method in DECLARED_METHODS and method not in DENIED_METHODS
            and not method.startswith(_DENIED_PREFIXES))


def _truthy(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"})


def _check_profile(request_id: Any, params: dict) -> None:
    profile = params.get("profile")
    if profile not in (None, "", "default"):
        raise PolicyRefusal(request_id, "Hermachy sessions are pinned to the default profile")


def rewrite_create(request_id: Any, params: dict, *, host_cwd: str, client_version: str,
                   reasoning: Optional[str] = None) -> dict:
    """Bind a new session to this connection's host workspace (the plugin's binding marker)."""
    for field in _BYPASS_FIELDS:
        if _truthy(params.get(field)):
            raise PolicyRefusal(request_id, f"{field} sessions are unavailable in Hermachy: they skip "
                                "the workstation route")
    for field in _FORBIDDEN_CREATE_FIELDS:
        if field in params:
            raise PolicyRefusal(request_id, f"session.create field {field!r} is unavailable in Hermachy")
    result = {key: value for key, value in params.items()
              if key not in _STRIPPED_CREATE_FIELDS and key not in _BYPASS_FIELDS}
    result["cwd"] = host_cwd
    result["client"] = {"name": "hermachy", "version": client_version}
    if reasoning and "reasoning" not in result:
        result["reasoning"] = reasoning
    return result


def client_frame(line: str, *, host_cwd: str, client_version: str,
                 reasoning: Optional[str] = None) -> Forward:
    """Check one client JSON-RPC frame. Responses to server requests pass through unchanged."""
    try:
        frame = json.loads(line)
    except ValueError as exc:
        raise PolicyRefusal(None, "client frame is not JSON") from exc
    if not isinstance(frame, dict):
        raise PolicyRefusal(None, "batched or non-object frames are unavailable in Hermachy")
    request_id = frame.get("id")
    method = frame.get("method")
    if method is None:
        if "result" in frame or "error" in frame:
            return Forward(line)  # the client's answer to a gateway request (approval, clarify, …)
        raise PolicyRefusal(request_id, "frame has no method")
    if not isinstance(method, str) or not method_allowed(method):
        raise PolicyRefusal(request_id, f"{method} is unavailable in Hermachy")
    params = frame.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise PolicyRefusal(request_id, "params must be an object")
    _check_profile(request_id, params)
    if method == "session.mutate" and (params.get("operation") == "workspace" or "workspace" in params):
        raise PolicyRefusal(request_id, "the workstation directory is fixed by the Hermachy client")
    if method == "session.create":
        frame = dict(frame, params=rewrite_create(request_id, params, host_cwd=host_cwd,
                                                  client_version=client_version, reasoning=reasoning))
        line = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
    return Forward(line, request_id, method if method in BINDING_METHODS else None)


def refusal_frame(refusal: PolicyRefusal) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": refusal.request_id,
                       "error": {"code": 4035, "message": str(refusal)}}, ensure_ascii=False)


def answered_sessions(line: str, request_id: Any) -> Optional[list[str]]:
    """Session ids in the gateway's successful answer to a pending binding request, else None."""
    try:
        frame = json.loads(line)
    except ValueError:
        return None
    if not isinstance(frame, dict) or "method" in frame or frame.get("id") != request_id:
        return None
    result = frame.get("result")
    if not isinstance(result, dict):
        return []
    ids = []
    for key in ("session_id", "stored_session_id"):
        value = result.get(key)
        if isinstance(value, str) and 0 < len(value) <= MAX_ID_TEXT and value not in ids:
            ids.append(value)
    return ids
