"""Omarchy side of a Hermachy attach: the local Ink TUI's WebSocket <-> one SSH channel.

The v0.23+ TUI dials ``HERMES_TUI_GATEWAY_URL``. Hermachy points it at this loopback listener
(127.0.0.1, ephemeral port, secret path). Each dial opens one restricted SSH channel to the
Hermes host's forced command (``attach``), which bridges to the default profile's gateway.
One text frame is one line on the channel. The TUI's own reconnects redial; the connection id
stays the same for the life of this process so reconnects keep the same workstation binding.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
from typing import Callable, Optional

from .protocol import LaunchRequest
from .websocket import WebSocket, WebSocketClosed, WebSocketError, server_handshake

HANDSHAKE_TIMEOUT = 10
LOG_PATH = Path("~/.local/state/hermachy/logs/relay.log")
SHUTDOWN_TIMEOUT = 5
MAX_MESSAGE = 512 * 1024 * 1024


def prepare_frame(text: str, reasoning: Optional[str]) -> str:
    """Supply the invocation's reasoning effort on new sessions (the TUI has no flag for it)."""
    if not reasoning or '"session.create"' not in text:
        return text
    try:
        frame = json.loads(text)
    except ValueError:
        return text
    if isinstance(frame, dict) and frame.get("method") == "session.create" and isinstance(frame.get("params", {}), dict):
        params = dict(frame.get("params") or {})
        params.setdefault("reasoning", reasoning)
        frame["params"] = params
        return json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
    return text


def _open_log():
    path = LOG_PATH.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    return os.fdopen(descriptor, "ab", buffering=0)


class Relay:
    """Loopback WebSocket listener; one active SSH channel per accepted TUI connection."""

    def __init__(self, ssh_argv: Callable[[LaunchRequest], list[str]], cwd: str, connection: str,
                 reasoning: Optional[str] = None):
        self._ssh_argv, self._cwd, self._connection, self._reasoning = ssh_argv, cwd, connection, reasoning
        self._path = "/" + secrets.token_urlsafe(32)
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(4)
        self._lock = threading.Lock()
        self._active: Optional[tuple[WebSocket, subprocess.Popen, socket.socket]] = None
        self._closed = False

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self._listener.getsockname()[1]}{self._path}"

    def start(self) -> "Relay":
        threading.Thread(target=self._accept_loop, name="hermachy-relay", daemon=True).start()
        return self

    def close(self) -> None:
        self._closed = True
        try:
            self._listener.close()
        except OSError:
            pass
        with self._lock:
            active, self._active = self._active, None
        if active:
            self._finish(*active)

    def _accept_loop(self) -> None:
        while not self._closed:
            try:
                conn, peer = self._listener.accept()
            except OSError:
                return
            if peer[0] != "127.0.0.1":
                conn.close()
                continue
            threading.Thread(target=self._serve, args=(conn,), name="hermachy-session", daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(HANDSHAKE_TIMEOUT)
            server_handshake(conn, expected_path=self._path)
            conn.settimeout(None)
        except (OSError, WebSocketError):
            conn.close()
            return
        ws = WebSocket(conn, is_client=False, max_message_size=MAX_MESSAGE)
        request = LaunchRequest(self._cwd, "attach", connection=self._connection)
        try:
            # SSH and the Hermes host's diagnostics must never draw over the TUI.
            log = _open_log()
            try:
                child = subprocess.Popen(self._ssh_argv(request), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=log)
            finally:
                log.close()
        except OSError as exc:
            with _open_log() as log:
                log.write(f"hermachy: cannot start SSH: {exc}\n".encode())
            self._finish(ws, None, conn)
            return
        with self._lock:
            previous, self._active = self._active, (ws, child, conn)
        if previous:
            self._finish(*previous)  # a TUI reconnect replaces the old channel
        threading.Thread(target=self._remote_to_local, args=(ws, child, conn), daemon=True).start()
        try:
            while True:
                message = ws.recv_message()
                if isinstance(message, bytes):
                    message = message.decode("utf-8")
                child.stdin.write(prepare_frame(message, self._reasoning).encode("utf-8") + b"\n")
                child.stdin.flush()
        except (WebSocketClosed, WebSocketError, OSError, ValueError):
            pass
        finally:
            with self._lock:
                if self._active == (ws, child, conn):
                    self._active = None
            self._finish(ws, child, conn)

    def _remote_to_local(self, ws: WebSocket, child: subprocess.Popen, conn: socket.socket) -> None:
        try:
            for raw in iter(child.stdout.readline, b""):
                line = raw.decode("utf-8").rstrip("\r\n")
                if line:
                    ws.send_text(line)
        except (OSError, WebSocketError, UnicodeDecodeError):
            pass
        finally:
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            code = 1000 if child.poll() in (None, 0) else 1011
            try:
                ws.close(code, "hermes host closed")
            except (OSError, WebSocketError):
                pass
            for step in (lambda: conn.shutdown(socket.SHUT_RDWR), conn.close):
                try:
                    step()
                except OSError:
                    pass

    @staticmethod
    def _finish(ws: WebSocket, child: Optional[subprocess.Popen], conn: socket.socket) -> None:
        try:
            ws.close(1000, "closed")
        except (OSError, WebSocketError):
            pass
        for step in (lambda: conn.shutdown(socket.SHUT_RDWR), conn.close):
            try:
                step()
            except OSError:
                pass
        if child is None:
            return
        try:
            child.stdin.close()
        except OSError:
            pass
        try:
            child.wait(timeout=SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            child.terminate()
            try:
                child.wait(timeout=SHUTDOWN_TIMEOUT)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        try:
            child.stdout.close()
        except OSError:
            pass
