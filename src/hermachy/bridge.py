"""Hermes-host side of a Hermachy attach: SSH stdio lines <-> the default profile's gateway.

``hermes gateway attach --json`` (the attach the Ink TUI itself uses) discovers the running
gateway and mints a 30-second single-use ticket. The bridge dials the gateway's loopback
WebSocket with it, filters every client frame through :mod:`hermachy.attach_policy`, and
records which sessions this connection created or resumed before their answer reaches the
client, so the Hermachy plugin routes them to the workstation from their first tool call.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import subprocess
import sys
import tempfile
import threading
from typing import Any, BinaryIO, Optional
from urllib.parse import urlsplit

from . import __version__
from .attach_policy import PolicyRefusal, answered_sessions, client_frame, refusal_frame
from .websocket import WebSocket, WebSocketClosed, WebSocketError, client_handshake

GATEWAY_PROTOCOL = "hermes-gateway-v1"
TICKET_PREFIX = "hermes-gateway-ticket."
MAX_MESSAGE = 512 * 1024 * 1024
ATTACH_TIMEOUT = 40
_CONNECTION_RE = re.compile(r"[a-f0-9]{32}")
_SESSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")


class BridgeError(RuntimeError):
    """The gateway could not be reached or refused the attach."""


@dataclass(frozen=True)
class Grant:
    host: str
    port: int
    path: str
    protocols: tuple[str, ...]
    profile_id: str
    instance_id: str


def _hermes_env(hermes_home: Optional[Path] = None) -> dict[str, str]:
    """A clean environment for the host's own ``hermes`` launcher (never sshd's or the client's)."""
    keep = {key: os.environ[key] for key in ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR")
            if key in os.environ}
    keep["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"
    if hermes_home is not None and Path(hermes_home).expanduser() != Path("~/.hermes").expanduser():
        keep["HERMES_HOME"] = str(hermes_home)
    return keep


def parse_grant(payload: Any) -> Grant:
    if not isinstance(payload, dict):
        raise BridgeError("invalid gateway attach answer")
    url, protocols = payload.get("url"), payload.get("protocols")
    parts = urlsplit(url if isinstance(url, str) else "")
    if parts.scheme != "ws" or parts.hostname not in {"127.0.0.1", "::1"} or not parts.port:
        raise BridgeError("the gateway did not offer a loopback WebSocket")
    if parts.path != "/api/ws" or parts.query or parts.fragment:
        raise BridgeError("unexpected gateway WebSocket path")
    if (not isinstance(protocols, list) or len(protocols) != 2 or GATEWAY_PROTOCOL not in protocols
            or not all(isinstance(p, str) for p in protocols)
            or not any(p.startswith(TICKET_PREFIX) and len(p) > len(TICKET_PREFIX) for p in protocols)):
        raise BridgeError("unexpected gateway attach protocols")
    endpoint = payload.get("endpoint") if isinstance(payload.get("endpoint"), dict) else {}
    return Grant(parts.hostname, parts.port, parts.path, tuple(protocols),
                 str(endpoint.get("profile_id") or payload.get("profile_id") or ""),
                 str(endpoint.get("instance_id") or payload.get("instance_id") or ""))


def obtain_grant(hermes_command: Path, hermes_home: Optional[Path] = None) -> Grant:
    """Discover the running gateway and mint one ticket (never starts a gateway under sshd)."""
    try:
        done = subprocess.run([str(hermes_command), "--profile", "default", "gateway", "attach", "--json"],
                              env=_hermes_env(hermes_home), cwd=str(Path.home()), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=ATTACH_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BridgeError(f"could not run the Hermes launcher: {type(exc).__name__}") from exc
    try:
        payload = json.loads(done.stdout.strip().splitlines()[-1]) if done.stdout.strip() else {}
    except ValueError:
        payload = {}
    if done.returncode != 0:
        message = payload.get("message") if isinstance(payload, dict) else None
        raise BridgeError((message or "the Hermes gateway is not attachable; check "
                           "`hermes gateway status` on the Hermes host").strip())
    return parse_grant(payload)


def dial(grant: Grant, timeout: Optional[float] = None) -> tuple[WebSocket, socket.socket]:
    """Open the gateway WebSocket; the caller owns (and must shut down) the socket."""
    sock = socket.create_connection((grant.host, grant.port), timeout=10)
    try:
        selected = client_handshake(sock, host=grant.host, port=grant.port, path=grant.path,
                                    subprotocols=list(grant.protocols))
        if selected != GATEWAY_PROTOCOL:
            raise BridgeError("the gateway did not accept the Hermes gateway protocol")
        sock.settimeout(timeout)
        return WebSocket(sock, is_client=True, max_message_size=MAX_MESSAGE), sock
    except BaseException:
        sock.close()
        raise


def shutdown(ws: WebSocket, sock: socket.socket, code: int = 1000, reason: str = "") -> None:
    """Send Close, then end the socket so a reader blocked in another thread wakes up."""
    try:
        ws.close(code, reason)
    except (OSError, WebSocketError):
        pass
    for step in (lambda: sock.shutdown(socket.SHUT_RDWR), sock.close):
        try:
            step()
        except OSError:
            pass


PREFLIGHT_ID = "hermachy-preflight"
PREFLIGHT_TIMEOUT = 20


def check_plugin(result: Any) -> None:
    """Fail closed unless the running gateway loaded this exact Hermachy plugin."""
    plugins = result.get("plugins") if isinstance(result, dict) else None
    entry = next((p for p in plugins or [] if isinstance(p, dict) and p.get("name") == "hermachy"), None)
    if entry is None or not entry.get("enabled"):
        raise BridgeError("the running Hermes gateway has not loaded the Hermachy plugin; run "
                          "`hermes gateway restart` on the Hermes host (after hermachy-admin studio-finish)")
    if entry.get("error"):
        raise BridgeError(f"the Hermachy plugin failed to load in the gateway: {entry['error']}")
    if entry.get("version") != __version__:
        raise BridgeError(f"the gateway runs Hermachy plugin {entry.get('version') or 'unknown'}; "
                          f"expected {__version__}. Re-run studio-finish and restart the gateway")


def preflight(ws: WebSocket) -> list[str]:
    """Ask the gateway which plugins it loaded before any client frame is relayed. Gateway frames
    that arrive first are held back and returned for the relay to deliver in order."""
    ws.send_text(json.dumps({"jsonrpc": "2.0", "id": PREFLIGHT_ID, "method": "plugins.list", "params": {}}))
    held: list[str] = []
    while True:
        message = ws.recv_message()
        if isinstance(message, bytes):
            continue
        try:
            frame = json.loads(message)
        except ValueError:
            held.append(message)
            continue
        if isinstance(frame, dict) and frame.get("id") == PREFLIGHT_ID and "method" not in frame:
            if "error" in frame:
                raise BridgeError("the gateway refused the Hermachy plugin check")
            check_plugin(frame.get("result"))
            return held
        held.append(message)


# ---------------------------------------------------------------- bindings

def _atomic_private_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class Bindings:
    """The records the Hermachy plugin reads: ``<hermes home>/hermachy/{connections,sessions,workspaces}``."""

    def __init__(self, hermes_home: Path):
        self.root = Path(hermes_home) / "hermachy"

    def _private_dir(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
        return path

    def prepare(self, connection: str, workstation_cwd: str) -> Path:
        """Record this connection's workstation directory and create its host workspace marker."""
        if not _CONNECTION_RE.fullmatch(connection):
            raise BridgeError("invalid connection id")
        if not workstation_cwd.startswith("/") or any(ord(c) < 32 or ord(c) == 127 for c in workstation_cwd):
            raise BridgeError("the workstation directory must be an absolute path")
        self._private_dir(self.root)
        workspace = self._private_dir(self._private_dir(self.root / "workspaces") / connection)
        self._private_dir(self.root / "connections")
        self._private_dir(self.root / "sessions")
        _atomic_private_json(self.root / "connections" / f"{connection}.json",
                             {"v": 1, "connection": connection, "cwd": str(PurePosixPath(workstation_cwd))})
        return workspace

    def bind_session(self, session_id: str, connection: str) -> bool:
        if not _SESSION_RE.fullmatch(session_id):
            return False
        _atomic_private_json(self.root / "sessions" / f"{session_id}.json", {"v": 1, "connection": connection})
        return True

    def latest_session_for(self, workstation_cwd: str) -> Optional[str]:
        """The most recently bound session whose connection worked in this workstation directory."""
        target = str(PurePosixPath(workstation_cwd))
        best: tuple[float, str] | None = None
        sessions = self.root / "sessions"
        if not sessions.is_dir():
            return None
        cwd_by_connection: dict[str, Optional[str]] = {}
        for path in sessions.glob("*.json"):
            try:
                connection = json.loads(path.read_text()).get("connection")
                if connection not in cwd_by_connection:
                    record = json.loads((self.root / "connections" / f"{connection}.json").read_text())
                    cwd_by_connection[connection] = record.get("cwd")
                if cwd_by_connection[connection] != target:
                    continue
                stamp = path.stat().st_mtime
            except (OSError, ValueError, AttributeError, TypeError):
                continue
            if best is None or stamp > best[0]:
                best = (stamp, path.stem)
        return best[1] if best else None


# ---------------------------------------------------------------- relay

class _Output:
    def __init__(self, stream: BinaryIO):
        self.stream, self.lock = stream, threading.Lock()

    def line(self, text: str) -> None:
        if "\n" in text or "\r" in text:
            text = json.dumps(json.loads(text), ensure_ascii=False, separators=(",", ":"))
        data = text.encode("utf-8") + b"\n"
        with self.lock:
            self.stream.write(data)
            self.stream.flush()


def relay(ws: WebSocket, sock: socket.socket, connection: str, host_workspace: Path, bindings: Bindings,
          stdin: BinaryIO, stdout: BinaryIO, held: Optional[list[str]] = None) -> int:
    output = _Output(stdout)
    for message in held or ():
        output.line(message)
    pending: dict[Any, str] = {}
    pending_lock = threading.Lock()
    finished = threading.Event()

    def client_to_gateway() -> None:
        try:
            for raw in iter(stdin.readline, b""):
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    forward = client_frame(line, host_cwd=str(host_workspace), client_version=__version__)
                except PolicyRefusal as refusal:
                    output.line(refusal_frame(refusal))
                    continue
                if forward.binding is not None:
                    with pending_lock:
                        pending[forward.request_id] = forward.binding
                ws.send_text(forward.frame)
        except (OSError, UnicodeDecodeError, WebSocketError):
            pass
        finally:
            finished.set()
            shutdown(ws, sock, 1000, "client closed")

    reader = threading.Thread(target=client_to_gateway, name="hermachy-client", daemon=True)
    reader.start()
    try:
        while True:
            message = ws.recv_message()
            if isinstance(message, bytes):
                continue  # the gateway never sends binary frames
            if pending:
                with pending_lock:
                    waiting = list(pending)
                for request_id in waiting:
                    sessions = answered_sessions(message, request_id)
                    if sessions is None:
                        continue
                    for session_id in sessions:
                        bindings.bind_session(session_id, connection)
                    with pending_lock:
                        pending.pop(request_id, None)
                    break
            output.line(message)
    except WebSocketClosed:
        return 0
    except (OSError, WebSocketError, ValueError) as exc:
        if finished.is_set():
            return 0  # the client closed first; the socket was shut down on purpose
        print(f"hermachy-server: gateway connection lost: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        try:
            stdout.flush()
        except OSError:
            pass


def run_attach(hermes_command: Path, hermes_home: Path, connection: str, workstation_cwd: str,
               stdin: BinaryIO, stdout: BinaryIO) -> int:
    bindings = Bindings(hermes_home)
    workspace = bindings.prepare(connection, workstation_cwd)
    ws, sock = dial(obtain_grant(hermes_command, hermes_home), timeout=PREFLIGHT_TIMEOUT)
    try:
        held = preflight(ws)
        sock.settimeout(None)
        return relay(ws, sock, connection, workspace, bindings, stdin, stdout, held)
    finally:
        shutdown(ws, sock, 1000, "bridge closed")


# ---------------------------------------------------------------- one-shot requests

def request_once(hermes_command: Path, method: str, params: dict, *, timeout: float = 20,
                 hermes_home: Optional[Path] = None) -> Any:
    """Attach, send one canonical request, return its result (events in between are skipped)."""
    ws, sock = dial(obtain_grant(hermes_command, hermes_home), timeout=timeout)
    try:
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": "hermachy-1", "method": method, "params": params}))
        while True:
            message = ws.recv_message()
            if isinstance(message, bytes):
                continue
            frame = json.loads(message)
            if isinstance(frame, dict) and frame.get("id") == "hermachy-1" and "method" not in frame:
                if "error" in frame:
                    error = frame["error"] if isinstance(frame["error"], dict) else {}
                    raise BridgeError(str(error.get("message") or "the gateway refused the request"))
                return frame.get("result")
    finally:
        shutdown(ws, sock, 1000, "done")
