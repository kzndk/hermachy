"""Minimal blocking RFC 6455 WebSocket endpoints for Hermachy's JSON relays.

Only what the relays need: the HTTP/1.1 upgrade handshake for each side and
unextended framing (never permessage-deflate) with fragmented receive,
automatic pongs and the closing handshake.

The caller owns the socket. It sets a timeout before a handshake function to
bound the whole handshake, leaves the socket blocking afterwards, and closes
it once ``WebSocket.recv_message`` reports the end of the connection.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
import socket
import struct
import threading
import time
from typing import Callable, NoReturn, Sequence

DEFAULT_MAX_MESSAGE_SIZE = 256 * 1024 * 1024
MAX_HEADER_BYTES = 16 * 1024
MAX_CONTROL_PAYLOAD = 125
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA
_OPCODES = frozenset({OP_CONTINUATION, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG})

CLOSE_NORMAL = 1000
CLOSE_PROTOCOL_ERROR = 1002
CLOSE_NO_STATUS = 1005
CLOSE_ABNORMAL = 1006
CLOSE_INVALID_DATA = 1007
CLOSE_TOO_BIG = 1009

_TOKEN_RE = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_VISIBLE_RE = re.compile(r"[\x21-\x7e]+")
_STATUS_RE = re.compile(r"[0-9]{3}")
_REASONS = {
    400: "Bad Request", 403: "Forbidden", 405: "Method Not Allowed",
    426: "Upgrade Required", 431: "Request Header Fields Too Large",
}
# Multiple of 4 so every chunk starts at the same masking-key phase.
_MASK_CHUNK = 16 * 1024
# Larger unmasked payloads are sent after their header instead of copied.
_SMALL_FRAME = 64 * 1024


class WebSocketError(Exception):
    """A WebSocket failure; ``code`` is the close code involved, if any."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class HandshakeError(WebSocketError):
    """The opening handshake failed; ``status`` is the HTTP status involved.

    On the server it is the status sent to the client (None sends nothing);
    on the client it is the non-101 status the server answered with.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class WebSocketClosed(WebSocketError):
    """The connection is closed, with the peer's close ``code`` and ``reason``.

    ``code`` is 1005 when the Close frame carried no status and 1006 when the
    connection ended without a Close frame.
    """

    def __init__(self, code: int, reason: str = "") -> None:
        detail = f"{code}: {reason}" if reason else str(code)
        super().__init__(f"WebSocket closed ({detail})", code)
        self.reason = reason


def compute_accept(key: str) -> str:
    """Return the ``Sec-WebSocket-Accept`` value for a ``Sec-WebSocket-Key``."""

    digest = hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii"), usedforsecurity=False)
    return base64.b64encode(digest.digest()).decode("ascii")


def server_handshake(
    sock: socket.socket,
    *,
    expected_path: str,
    select_subprotocol: Callable[[list[str]], str | None] | None = None,
    max_header_bytes: int = MAX_HEADER_BYTES,
) -> str | None:
    """Answer a client's upgrade request and return the selected subprotocol.

    The request target must equal ``expected_path`` (403 otherwise).
    ``select_subprotocol`` receives the offered subprotocols in client order,
    possibly none, and returns one of them or None; it may raise
    HandshakeError to refuse the client with that status. Without it no
    subprotocol is selected. On failure an HTTP error is sent where one
    applies, the socket is closed and the error is re-raised.
    """

    if not expected_path.startswith("/"):
        raise ValueError("expected_path must start with '/'")
    try:
        key, offered = _check_request(_read_head(sock, max_header_bytes), expected_path)
        selected = select_subprotocol(offered) if select_subprotocol else None
        if selected is not None and selected not in offered:
            raise ValueError("select_subprotocol must return an offered subprotocol or None")
        lines = [
            "HTTP/1.1 101 Switching Protocols", "Upgrade: websocket",
            "Connection: Upgrade", f"Sec-WebSocket-Accept: {compute_accept(key)}",
        ]
        if selected is not None:
            lines.append(f"Sec-WebSocket-Protocol: {selected}")
        _send_head(sock, lines)
        return selected
    except HandshakeError as exc:
        if exc.status is not None:
            _send_rejection(sock, exc.status)
        sock.close()
        raise
    except BaseException:
        sock.close()
        raise


def client_handshake(
    sock: socket.socket,
    *,
    host: str,
    port: int,
    path: str,
    subprotocols: Sequence[str] = (),
    max_header_bytes: int = MAX_HEADER_BYTES,
) -> str | None:
    """Upgrade a connected socket and return the subprotocol the server chose.

    Sends no ``Origin`` and offers no extensions. Returns None when the server
    selected no subprotocol; callers that require one must check. On failure
    the socket is closed and HandshakeError is raised.
    """

    if not path.startswith("/") or not _VISIBLE_RE.fullmatch(path):
        raise ValueError("path must be an absolute request target without spaces")
    if not _VISIBLE_RE.fullmatch(host):
        raise ValueError("host must be a non-empty name without spaces")
    if not 0 < port < 65536:
        raise ValueError("port must be between 1 and 65535")
    if isinstance(subprotocols, str):
        raise TypeError("subprotocols must be a sequence of strings")
    offered = list(subprotocols)
    if not all(_TOKEN_RE.fullmatch(protocol) for protocol in offered):
        raise ValueError("subprotocols must be HTTP tokens")

    key = base64.b64encode(os.urandom(16)).decode("ascii")
    authority = f"[{host}]" if ":" in host and not host.startswith("[") else host
    lines = [
        f"GET {path} HTTP/1.1", f"Host: {authority}:{port}", "Upgrade: websocket",
        "Connection: Upgrade", f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13",
    ]
    if offered:
        lines.append("Sec-WebSocket-Protocol: " + ", ".join(offered))
    try:
        _send_head(sock, lines)
        status_line, headers = _parse_head(_read_head(sock, max_header_bytes), None)
        return _check_response(status_line, headers, key, offered)
    except BaseException:
        sock.close()
        raise


def _check_request(head: bytes, expected_path: str) -> tuple[str, list[str]]:
    request_line, headers = _parse_head(head, 400)
    parts = request_line.split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/"):
        raise HandshakeError("malformed request line", 400)
    method, target, version = parts
    # Check the secret first so unauthenticated probes learn nothing else.
    if not hmac.compare_digest(target.encode("latin-1"), expected_path.encode("utf-8")):
        raise HandshakeError("unexpected request path", 403)
    if method != "GET":
        raise HandshakeError(f"unsupported method {method!r}", 405)
    if version != "HTTP/1.1":
        raise HandshakeError(f"unsupported HTTP version {version!r}", 400)
    if not headers.get("host"):
        raise HandshakeError("missing Host header", 400)
    if "websocket" not in _lower_tokens(headers, "upgrade"):
        raise HandshakeError("missing 'Upgrade: websocket'", 400)
    if "upgrade" not in _lower_tokens(headers, "connection"):
        raise HandshakeError("Connection header does not include 'upgrade'", 400)
    if headers.get("sec-websocket-version") != "13":
        raise HandshakeError("unsupported Sec-WebSocket-Version", 426)
    key = headers.get("sec-websocket-key", "")
    if not _valid_key(key):
        raise HandshakeError("invalid Sec-WebSocket-Key", 400)
    offered = _tokens(headers, "sec-websocket-protocol")
    if not all(_TOKEN_RE.fullmatch(protocol) for protocol in offered):
        raise HandshakeError("invalid Sec-WebSocket-Protocol", 400)
    return key, offered


def _check_response(
    status_line: str, headers: dict[str, str], key: str, offered: list[str]
) -> str | None:
    parts = status_line.split(" ", 2)
    if len(parts) < 2 or parts[0] != "HTTP/1.1" or not _STATUS_RE.fullmatch(parts[1]):
        raise HandshakeError(f"malformed status line {status_line[:80]!r}")
    if parts[1] != "101":
        raise HandshakeError(f"server refused the upgrade: {status_line[:80]}", int(parts[1]))
    if "websocket" not in _lower_tokens(headers, "upgrade"):
        raise HandshakeError("response lacks 'Upgrade: websocket'")
    if "upgrade" not in _lower_tokens(headers, "connection"):
        raise HandshakeError("response Connection header does not include 'upgrade'")
    if headers.get("sec-websocket-accept") != compute_accept(key):
        raise HandshakeError("Sec-WebSocket-Accept does not match the key")
    if headers.get("sec-websocket-extensions"):
        raise HandshakeError("server enabled an extension that was not offered")
    selected = headers.get("sec-websocket-protocol")
    if selected is not None and selected not in offered:
        raise HandshakeError(f"server selected an unoffered subprotocol {selected[:80]!r}")
    return selected


def _valid_key(key: str) -> bool:
    try:
        return len(key) == 24 and len(base64.b64decode(key, validate=True)) == 16
    except (binascii.Error, ValueError):
        return False


def _tokens(headers: dict[str, str], name: str) -> list[str]:
    items = (item.strip(" \t") for item in headers.get(name, "").split(","))
    return [item for item in items if item]


def _lower_tokens(headers: dict[str, str], name: str) -> list[str]:
    return [item.lower() for item in _tokens(headers, name)]


def _parse_head(head: bytes, status: int | None) -> tuple[str, dict[str, str]]:
    """Split an HTTP head into its start line and lower-cased, merged headers."""

    lines = head[:-4].decode("latin-1").split("\r\n")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, colon, value = line.partition(":")
        if not colon or not _TOKEN_RE.fullmatch(name):
            raise HandshakeError(f"malformed header line {line[:80]!r}", status)
        name, value = name.lower(), value.strip(" \t")
        headers[name] = f"{headers[name]}, {value}" if name in headers else value
    return lines[0], headers


def _read_head(sock: socket.socket, limit: int) -> bytes:
    """Consume one HTTP head, leaving any frames that follow it unread.

    The socket's timeout bounds the total time, not each read.
    """

    timeout = sock.gettimeout()
    deadline = None if timeout is None else time.monotonic() + timeout
    head = bytearray()
    try:
        while True:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise HandshakeError("handshake timed out")
                sock.settimeout(remaining)
            peeked = sock.recv(limit - len(head), socket.MSG_PEEK)
            if not peeked:
                raise EOFError
            carried = min(len(head), 3)
            end = (bytes(head[len(head) - carried:]) + peeked).find(b"\r\n\r\n")
            head += _recv_exactly(sock, len(peeked) if end < 0 else end + 4 - carried)
            if end >= 0:
                return bytes(head)
            if len(head) >= limit:
                raise HandshakeError("handshake header is too large", 431)
    except EOFError:
        raise HandshakeError("connection closed during the handshake") from None
    except socket.timeout as exc:
        raise HandshakeError("handshake timed out") from exc
    except OSError as exc:
        raise HandshakeError(f"handshake failed: {exc}") from exc
    finally:
        sock.settimeout(timeout)


def _send_head(sock: socket.socket, lines: list[str]) -> None:
    try:
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
    except OSError as exc:
        raise HandshakeError(f"handshake failed: {exc}") from exc


def _send_rejection(sock: socket.socket, status: int) -> None:
    lines = [f"HTTP/1.1 {status} {_REASONS.get(status, 'Error')}",
             "Connection: close", "Content-Length: 0"]
    if status == 426:
        lines.append("Sec-WebSocket-Version: 13")
    try:
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
    except OSError:
        pass


def _recv_exactly(sock: socket.socket, size: int) -> bytearray:
    buffer = bytearray(size)
    with memoryview(buffer) as view:
        received = 0
        while received < size:
            count = sock.recv_into(view[received:])
            if not count:
                raise EOFError
            received += count
    return buffer


def _mask(view: memoryview, key: bytes) -> None:
    """XOR ``view`` in place with the repeating 4-byte masking ``key``."""

    size = len(view)
    whole = size - size % _MASK_CHUNK
    if whole:
        pattern = int.from_bytes(key * (_MASK_CHUNK // 4), "little")
        for start in range(0, whole, _MASK_CHUNK):
            chunk = view[start:start + _MASK_CHUNK]
            chunk[:] = (int.from_bytes(chunk, "little") ^ pattern).to_bytes(_MASK_CHUNK, "little")
    rest = size - whole
    if rest:
        pattern = int.from_bytes((key * (rest // 4 + 1))[:rest], "little")
        chunk = view[whole:]
        chunk[:] = (int.from_bytes(chunk, "little") ^ pattern).to_bytes(rest, "little")


def _valid_close_code(code: int) -> bool:
    return 1000 <= code <= 1003 or 1007 <= code <= 1014 or 3000 <= code <= 4999


class WebSocket:
    """A blocking WebSocket endpoint on an already upgraded socket.

    One thread receives while any number of threads send; sends are
    serialised by a lock. ``on_pong`` is called from the receiving thread
    with each Pong payload. A socket error or timeout ends the connection.
    """

    def __init__(
        self,
        sock: socket.socket,
        *,
        is_client: bool,
        max_message_size: int = DEFAULT_MAX_MESSAGE_SIZE,
        on_pong: Callable[[bytes], None] | None = None,
    ) -> None:
        if max_message_size < 1:
            raise ValueError("max_message_size must be positive")
        self._sock = sock
        self._is_client = is_client
        self._max_message_size = max_message_size
        self._on_pong = on_pong
        self._send_lock = threading.Lock()
        self._close_sent: tuple[int, str] | None = None
        self._ended: WebSocketError | None = None

    def fileno(self) -> int:
        return self._sock.fileno()

    def recv_message(self) -> str | bytes:
        """Return the next text (str) or binary (bytes) message.

        Answers pings and the peer's Close frame on the way. Raises
        WebSocketClosed when the connection has ended and WebSocketError,
        with the close code sent to the peer, on a protocol violation.
        """

        if self._ended is not None:
            raise self._ended.with_traceback(None)
        try:
            return self._recv_message()
        except WebSocketError as exc:
            self._ended = exc
            raise

    def send_text(self, text: str | bytes) -> None:
        """Send one text message; ``bytes`` must already be UTF-8."""

        if isinstance(text, str):
            payload = text.encode("utf-8")
        else:
            str(text, "utf-8")  # Validate before the peer has to reject it.
            payload = text
        self._send_frame(OP_TEXT, payload)

    def send_binary(self, data: bytes) -> None:
        self._send_frame(OP_BINARY, data)

    def ping(self, data: bytes = b"") -> None:
        if len(data) > MAX_CONTROL_PAYLOAD:
            raise ValueError("ping payload exceeds 125 bytes")
        self._send_frame(OP_PING, data)

    def close(self, code: int = CLOSE_NORMAL, reason: str = "") -> None:
        """Start the closing handshake; later sends raise WebSocketClosed.

        Does nothing if a Close frame was already sent. ``recv_message``
        keeps returning messages until the peer's Close arrives.
        """

        if not _valid_close_code(code):
            raise ValueError(f"invalid close code {code}")
        payload = struct.pack("!H", code) + reason.encode("utf-8")
        if len(payload) > MAX_CONTROL_PAYLOAD:
            raise ValueError("close reason exceeds 123 bytes")
        self._send_frame(OP_CLOSE, payload, closing=(code, reason))

    def _recv_message(self) -> str | bytes:
        opcode: int | None = None
        message = bytearray()
        while True:
            fin, frame_opcode, payload = self._recv_frame(len(message))
            if frame_opcode >= OP_CLOSE:
                self._handle_control(frame_opcode, payload)
                continue
            if frame_opcode == OP_CONTINUATION:
                if opcode is None:
                    self._fail(CLOSE_PROTOCOL_ERROR, "unexpected continuation frame")
                message += payload
            elif opcode is not None:
                self._fail(CLOSE_PROTOCOL_ERROR, "expected a continuation frame")
            else:
                opcode, message = frame_opcode, payload
            if fin:
                break
        if opcode == OP_BINARY:
            return bytes(message)
        try:
            return message.decode("utf-8")
        except UnicodeDecodeError:
            self._fail(CLOSE_INVALID_DATA, "text message is not valid UTF-8")

    def _recv_frame(self, buffered: int) -> tuple[bool, int, bytearray]:
        first, second = self._read(2)
        fin, opcode, length = bool(first & 0x80), first & 0x0F, second & 0x7F
        if first & 0x70:
            self._fail(CLOSE_PROTOCOL_ERROR, "reserved bits set without an extension")
        if opcode not in _OPCODES:
            self._fail(CLOSE_PROTOCOL_ERROR, f"unknown opcode {opcode:#x}")
        if opcode >= OP_CLOSE and not fin:
            self._fail(CLOSE_PROTOCOL_ERROR, "fragmented control frame")
        if opcode >= OP_CLOSE and length > MAX_CONTROL_PAYLOAD:
            self._fail(CLOSE_PROTOCOL_ERROR, "control frame payload exceeds 125 bytes")
        masked = bool(second & 0x80)
        if masked == self._is_client:
            self._fail(CLOSE_PROTOCOL_ERROR, "server frames must not be masked"
                       if self._is_client else "client frames must be masked")
        if length == 126:
            (length,) = struct.unpack("!H", self._read(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", self._read(8))
            if length >> 63:
                self._fail(CLOSE_PROTOCOL_ERROR, "payload length exceeds 63 bits")
        if opcode < OP_CLOSE and length > self._max_message_size - buffered:
            self._fail(CLOSE_TOO_BIG, f"message exceeds {self._max_message_size} bytes")
        key = bytes(self._read(4)) if masked else b""
        payload = self._read(length)
        if key and length:
            with memoryview(payload) as view:
                _mask(view, key)
        return fin, opcode, payload

    def _handle_control(self, opcode: int, payload: bytearray) -> None:
        if opcode == OP_PING:
            self._send_frame(OP_PONG, payload)
        elif opcode == OP_PONG:
            if self._on_pong is not None:
                self._on_pong(bytes(payload))
        else:
            code, reason = self._parse_close(payload)
            echo = b"" if code == CLOSE_NO_STATUS else struct.pack("!H", code)
            self._send_frame(OP_CLOSE, echo, closing=(code, reason))
            raise WebSocketClosed(code, reason)

    def _parse_close(self, payload: bytearray) -> tuple[int, str]:
        if not payload:
            return CLOSE_NO_STATUS, ""
        if len(payload) < 2:
            self._fail(CLOSE_PROTOCOL_ERROR, "close frame payload is too short")
        (code,) = struct.unpack_from("!H", payload)
        if not _valid_close_code(code):
            self._fail(CLOSE_PROTOCOL_ERROR, f"invalid close code {code}")
        try:
            return code, payload[2:].decode("utf-8")
        except UnicodeDecodeError:
            self._fail(CLOSE_INVALID_DATA, "close reason is not valid UTF-8")

    def _fail(self, code: int, message: str) -> NoReturn:
        """Send a Close frame for a protocol violation and raise it."""

        reason = message.encode("ascii", "replace")[:MAX_CONTROL_PAYLOAD - 2]
        try:
            self._send_frame(OP_CLOSE, struct.pack("!H", code) + reason, closing=(code, message))
        except WebSocketError:
            pass
        raise WebSocketError(message, code)

    def _read(self, size: int) -> bytearray:
        try:
            return _recv_exactly(self._sock, size)
        except EOFError:
            raise WebSocketClosed(CLOSE_ABNORMAL, "connection ended without a close frame") from None
        except OSError as exc:
            raise WebSocketClosed(CLOSE_ABNORMAL, str(exc)) from exc

    def _frame(self, opcode: int, payload: bytes) -> list[bytes]:
        length = len(payload)
        first, mask_bit = 0x80 | opcode, 0x80 if self._is_client else 0
        if length < 126:
            header = struct.pack("!BB", first, mask_bit | length)
        elif length < 1 << 16:
            header = struct.pack("!BBH", first, mask_bit | 126, length)
        else:
            header = struct.pack("!BBQ", first, mask_bit | 127, length)
        if self._is_client:
            key = os.urandom(4)
            frame = bytearray(header + key)
            frame += payload
            with memoryview(frame) as view:
                _mask(view[len(header) + 4:], key)
            return [frame]
        if length <= _SMALL_FRAME:
            return [header + payload]
        return [header, payload]

    def _send_frame(
        self, opcode: int, payload: bytes, *, closing: tuple[int, str] | None = None
    ) -> None:
        """Send one frame; ``closing`` marks a Close frame and records its status.

        Once a Close frame has gone out, further Close and Pong frames are
        dropped and data frames and pings raise WebSocketClosed.
        """

        parts = self._frame(opcode, payload)
        with self._send_lock:
            if self._close_sent is not None:
                if closing is not None or opcode == OP_PONG:
                    return
                raise WebSocketClosed(*self._close_sent)
            try:
                for part in parts:
                    self._sock.sendall(part)
            except OSError as exc:
                self._close_sent = (CLOSE_ABNORMAL, str(exc))
                raise WebSocketClosed(CLOSE_ABNORMAL, str(exc)) from exc
            if closing is not None:
                self._close_sent = closing
