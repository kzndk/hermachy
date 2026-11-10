from __future__ import annotations

import base64
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
import socket
import struct
import threading
import time
import unittest

from hermachy.websocket import (
    HandshakeError,
    WebSocket,
    WebSocketClosed,
    WebSocketError,
    client_handshake,
    compute_accept,
    server_handshake,
)

TIMEOUT = 5.0
PATH = "/hermachy/3f2a9c0d5e7b41a8"
SAMPLE_KEY = "dGhlIHNhbXBsZSBub25jZQ=="
KEY = b"\x9a\x3c\x5e\x7f"
OFFER = ["hermes-gateway-v1", "hermes-gateway-ticket.tok3n"]
TEXT, BINARY, CLOSE, PING, PONG = 0x1, 0x2, 0x8, 0x9, 0xA

RawFrame = namedtuple("RawFrame", "fin opcode masked indicator payload")


def xor(data: bytes, key: bytes) -> bytes:
    return bytes(byte ^ key[index % 4] for index, byte in enumerate(data))


def frame(opcode: int, payload: bytes = b"", *, fin: bool = True, key: bytes | None = KEY,
          rsv: int = 0, length: int | None = None) -> bytes:
    """Encode a raw frame, masked with ``key`` unless it is None."""

    size = len(payload) if length is None else length
    mask_bit = 0 if key is None else 0x80
    head = bytes([(0x80 if fin else 0) | rsv | opcode])
    if size < 126:
        head += bytes([mask_bit | size])
    elif size < 1 << 16:
        head += bytes([mask_bit | 126]) + struct.pack("!H", size)
    else:
        head += bytes([mask_bit | 127]) + struct.pack("!Q", size)
    return head + payload if key is None else head + key + xor(payload, key)


def close_payload(code: int, reason: bytes = b"") -> bytes:
    return struct.pack("!H", code) + reason


def recv_exactly(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


def read_frame(sock: socket.socket) -> RawFrame:
    first, second = recv_exactly(sock, 2)
    indicator = size = second & 0x7F
    if indicator == 126:
        (size,) = struct.unpack("!H", recv_exactly(sock, 2))
    elif indicator == 127:
        (size,) = struct.unpack("!Q", recv_exactly(sock, 8))
    key = recv_exactly(sock, 4) if second & 0x80 else None
    payload = recv_exactly(sock, size)
    return RawFrame(bool(first & 0x80), first & 0x0F, key is not None, indicator,
                    payload if key is None else xor(payload, key))


def read_head(sock: socket.socket) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data:
        try:
            chunk = sock.recv(1)
        except ConnectionResetError:
            break
        if not chunk:
            break
        data += chunk
    return data


def fields(head: bytes) -> dict[str, str]:
    lines = head.decode("latin-1").split("\r\n")[1:]
    return {name.lower(): value.strip() for name, _, value in
            (line.partition(":") for line in lines if line)}


def request(path: str = PATH, *, request_line: str | None = None,
            headers: dict | None = None, extra: tuple = ()) -> bytes:
    values = {"Host": "127.0.0.1:8765", "Upgrade": "websocket", "Connection": "Upgrade",
              "Sec-WebSocket-Key": SAMPLE_KEY, "Sec-WebSocket-Version": "13"}
    values.update(headers or {})
    lines = [request_line or f"GET {path} HTTP/1.1"]
    lines += [f"{name}: {value}" for name, value in values.items() if value is not None]
    lines += list(extra)
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def switching(head: bytes, *extra: str, accept: str | None = None) -> bytes:
    accept = accept or compute_accept(fields(head)["sec-websocket-key"])
    lines = ["HTTP/1.1 101 Switching Protocols", "Upgrade: websocket",
             "Connection: Upgrade", f"Sec-WebSocket-Accept: {accept}", *extra]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


class SocketTestCase(unittest.TestCase):
    def pair(self) -> tuple[socket.socket, socket.socket]:
        left, right = socket.socketpair()
        for sock in (left, right):
            sock.settimeout(TIMEOUT)
            self.addCleanup(sock.close)
        return left, right

    def assert_eof(self, sock: socket.socket) -> None:
        try:
            self.assertEqual(sock.recv(1), b"")
        except ConnectionResetError:
            pass

    def raw_server(self, **options) -> tuple[WebSocket, socket.socket]:
        """A server endpoint driven by a raw, hand-framing client socket."""

        server, raw = self.pair()
        return WebSocket(server, is_client=False, **options), raw

    def raw_client(self, **options) -> tuple[WebSocket, socket.socket]:
        client, raw = self.pair()
        return WebSocket(client, is_client=True, **options), raw

    def open_pair(self, select=None, subprotocols=(), **options):
        """Handshake over real loopback TCP and return both endpoints."""

        with socket.create_server(("127.0.0.1", 0)) as listener:
            client = socket.create_connection(listener.getsockname(), timeout=TIMEOUT)
            server, _ = listener.accept()
            port = listener.getsockname()[1]
        server.settimeout(TIMEOUT)
        self.addCleanup(server.close)
        self.addCleanup(client.close)
        with ThreadPoolExecutor(1) as pool:
            accepted = pool.submit(server_handshake, server, expected_path=PATH,
                                   select_subprotocol=select)
            chosen = client_handshake(client, host="127.0.0.1", port=port, path=PATH,
                                      subprotocols=subprotocols)
            self.assertEqual(accepted.result(TIMEOUT), chosen)
        self.chosen = chosen
        return (WebSocket(server, is_client=False, **options),
                WebSocket(client, is_client=True, **options))


class ServerHandshakeTests(SocketTestCase):
    def serve(self, raw: bytes, **options):
        server, client = self.pair()
        client.sendall(raw)
        try:
            result = server_handshake(server, expected_path=PATH, **options)
        except HandshakeError as exc:
            self.assertEqual(server.fileno(), -1)
            result = exc
        return result, read_head(client), client

    def assert_rejected(self, raw: bytes, status: int, **options) -> bytes:
        result, response, client = self.serve(raw, **options)
        self.assertIsInstance(result, HandshakeError)
        self.assertEqual(result.status, status)
        self.assertTrue(response.startswith(f"HTTP/1.1 {status} ".encode()), response)
        self.assert_eof(client)
        return response

    def test_accept_matches_rfc_6455_sample(self):
        self.assertEqual(compute_accept(SAMPLE_KEY), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_accepts_valid_request(self):
        result, response, _ = self.serve(request())
        self.assertIsNone(result)
        self.assertTrue(response.startswith(b"HTTP/1.1 101 Switching Protocols\r\n"))
        headers = fields(response)
        self.assertEqual(headers["sec-websocket-accept"], "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")
        self.assertEqual(headers["upgrade"], "websocket")
        self.assertNotIn("sec-websocket-protocol", headers)

    def test_header_matching_is_case_insensitive_and_list_aware(self):
        raw = request(headers={"Upgrade": None, "Connection": None, "Sec-WebSocket-Key": None},
                      extra=("upgrade: WebSocket", "CONNECTION: keep-alive, UPGRADE",
                             f"sec-websocket-key: {SAMPLE_KEY}"))
        result, response, _ = self.serve(raw)
        self.assertIsNone(result)
        self.assertIn(b" 101 ", response)

    def test_extensions_are_ignored(self):
        raw = request(headers={"Sec-WebSocket-Extensions": "permessage-deflate; client_max_window_bits"})
        _, response, _ = self.serve(raw)
        self.assertIn(b" 101 ", response)
        self.assertNotIn("sec-websocket-extensions", fields(response))

    def test_wrong_path_is_forbidden(self):
        for path in ("/", PATH[:-1], PATH + "/", PATH + "?x=1", PATH.upper(), "/api/ws"):
            with self.subTest(path=path):
                self.assert_rejected(request(path), 403)
        # The secret path is checked before anything else is revealed.
        self.assert_rejected(request(request_line="POST /guess HTTP/1.1"), 403)

    def test_rejects_invalid_requests(self):
        cases = [
            (request(request_line=f"POST {PATH} HTTP/1.1"), 405),
            (request(request_line=f"GET {PATH} HTTP/1.0"), 400),
            (request(request_line=f"GET {PATH}"), 400),
            (request(headers={"Host": None}), 400),
            (request(headers={"Upgrade": None}), 400),
            (request(headers={"Upgrade": "h2c"}), 400),
            (request(headers={"Connection": None}), 400),
            (request(headers={"Connection": "keep-alive"}), 400),
            (request(headers={"Sec-WebSocket-Key": None}), 400),
            (request(headers={"Sec-WebSocket-Key": "not base64!!!!!!!!!!!!=="}), 400),
            (request(headers={"Sec-WebSocket-Key": base64.b64encode(b"x" * 15).decode()}), 400),
            (request(extra=(f"Sec-WebSocket-Key: {SAMPLE_KEY}",)), 400),
            (request(extra=("no colon here",)), 400),
            (request(extra=("Bad Name: x",)), 400),
            (request(headers={"Sec-WebSocket-Protocol": "good, bad token"}), 400),
            (request(headers={"Sec-WebSocket-Version": None}), 426),
        ]
        for raw, status in cases:
            with self.subTest(raw=raw, status=status):
                self.assert_rejected(raw, status)

    def test_unsupported_version_advertises_13(self):
        response = self.assert_rejected(request(headers={"Sec-WebSocket-Version": "8"}), 426)
        self.assertEqual(fields(response)["sec-websocket-version"], "13")

    def test_oversized_header_is_rejected(self):
        raw = request(headers={"X-Padding": "a" * 17000})
        self.assert_rejected(raw, 431)
        _, response, _ = self.serve(request(headers={"X-Padding": "a" * 900}), max_header_bytes=1024)
        self.assertIn(b" 431 ", response)

    def test_subprotocol_selection(self):
        offers = []

        def select(offered):
            offers.append(offered)
            return "hermes-gateway-v1" if "hermes-gateway-v1" in offered else None

        raw = request(headers={"Sec-WebSocket-Protocol": ", ".join(OFFER)})
        result, response, _ = self.serve(raw, select_subprotocol=select)
        self.assertEqual(result, "hermes-gateway-v1")
        self.assertEqual(offers, [OFFER])
        self.assertEqual(fields(response)["sec-websocket-protocol"], "hermes-gateway-v1")

        result, response, _ = self.serve(request(), select_subprotocol=select)
        self.assertIsNone(result)
        self.assertEqual(offers[-1], [])
        self.assertNotIn("sec-websocket-protocol", fields(response))

    def test_selector_can_refuse_and_must_choose_an_offer(self):
        def refuse(offered):
            raise HandshakeError("missing ticket", 403)

        self.assert_rejected(request(), 403, select_subprotocol=refuse)
        server, client = self.pair()
        client.sendall(request(headers={"Sec-WebSocket-Protocol": "a"}))
        with self.assertRaises(ValueError):
            server_handshake(server, expected_path=PATH, select_subprotocol=lambda _: "b")
        self.assertEqual(server.fileno(), -1)

    def test_handshake_time_is_bounded_in_total(self):
        # Trickle bytes for most of the timeout, then stall: a per-read
        # timeout would allow almost twice as long.
        server, client = self.pair()
        server.settimeout(0.6)
        stop = threading.Event()

        def drip():
            for byte in request()[:10]:
                if stop.wait(0.05):
                    return
                try:
                    client.sendall(bytes([byte]))
                except OSError:
                    return

        thread = threading.Thread(target=drip)
        thread.start()
        started = time.monotonic()
        try:
            with self.assertRaisesRegex(HandshakeError, "timed out") as caught:
                server_handshake(server, expected_path=PATH)
        finally:
            stop.set()
            thread.join()
        self.assertLess(time.monotonic() - started, 0.9)
        self.assertIsNone(caught.exception.status)
        self.assert_eof(client)

    def test_frames_sent_right_after_the_request_are_kept(self):
        server, client = self.pair()
        client.sendall(request() + frame(TEXT, b"early"))
        server_handshake(server, expected_path=PATH)
        self.assertEqual(WebSocket(server, is_client=False).recv_message(), "early")


class ClientHandshakeTests(SocketTestCase):
    def dial(self, respond, subprotocols=OFFER):
        server, client = self.pair()
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(client_handshake, client, host="127.0.0.1", port=8642,
                                 path="/api/ws", subprotocols=subprotocols)
            head = read_head(server)
            server.sendall(respond(head))
            try:
                return head, future.result(TIMEOUT), client, server
            except HandshakeError as exc:
                self.assertEqual(client.fileno(), -1)
                return head, exc, client, server

    def test_request_headers(self):
        head, result, _, _ = self.dial(lambda head: switching(head, "Sec-WebSocket-Protocol: hermes-gateway-v1"))
        self.assertEqual(result, "hermes-gateway-v1")
        self.assertTrue(head.startswith(b"GET /api/ws HTTP/1.1\r\n"))
        headers = fields(head)
        self.assertEqual(headers["host"], "127.0.0.1:8642")
        self.assertNotIn("origin", headers)
        self.assertNotIn("sec-websocket-extensions", headers)
        self.assertEqual(headers["sec-websocket-protocol"],
                         "hermes-gateway-v1, hermes-gateway-ticket.tok3n")
        self.assertEqual(headers["sec-websocket-version"], "13")
        self.assertEqual(headers["upgrade"], "websocket")
        self.assertEqual(headers["connection"], "Upgrade")
        self.assertEqual(len(base64.b64decode(headers["sec-websocket-key"], validate=True)), 16)

    def test_no_subprotocols_and_no_selection(self):
        head, result, _, _ = self.dial(switching, subprotocols=[])
        self.assertIsNone(result)
        self.assertNotIn("sec-websocket-protocol", fields(head))
        _, result, _, _ = self.dial(switching)
        self.assertIsNone(result)

    def test_rejects_bad_responses(self):
        cases = [
            lambda head: switching(head, accept=compute_accept(SAMPLE_KEY)),
            lambda head: switching(head, "Sec-WebSocket-Protocol: other"),
            lambda head: switching(head, "Sec-WebSocket-Extensions: permessage-deflate"),
            lambda head: switching(head).replace(b"Upgrade: websocket", b"Upgrade: h2c"),
            lambda head: switching(head).replace(b"Connection: Upgrade", b"Connection: close"),
            lambda head: b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n",
            lambda head: b"garbage\r\n\r\n",
        ]
        for respond in cases:
            with self.subTest(response=respond(request())):
                _, result, _, _ = self.dial(respond)
                self.assertIsInstance(result, HandshakeError)
        _, result, _, _ = self.dial(lambda head: b"HTTP/1.1 403 Forbidden\r\n\r\n")
        self.assertEqual(result.status, 403)
        _, result, _, _ = self.dial(lambda head: switching(head, "Sec-WebSocket-Protocol: x"),
                                    subprotocols=[])
        self.assertIsInstance(result, HandshakeError)

    def test_frames_sent_right_after_101_are_kept(self):
        _, result, client, _ = self.dial(
            lambda head: switching(head) + frame(TEXT, b"hello", key=None))
        self.assertIsNone(result)
        self.assertEqual(WebSocket(client, is_client=True).recv_message(), "hello")

    def test_invalid_arguments(self):
        server, client = self.pair()
        for options in ({"path": "api/ws"}, {"path": "/a b"}, {"subprotocols": ["a b"]},
                        {"subprotocols": ["a,b"]}, {"port": 0}, {"host": ""}):
            arguments = {"host": "127.0.0.1", "port": 1, "path": "/", "subprotocols": [], **options}
            with self.subTest(options=options), self.assertRaises(ValueError):
                client_handshake(client, **arguments)


class MessageTests(SocketTestCase):
    def test_handshake_and_round_trip_over_loopback(self):
        offers = []

        def select(offered):
            offers.append(offered)
            return offered[0]

        server, client = self.open_pair(select=select, subprotocols=OFFER)
        self.assertEqual(self.chosen, "hermes-gateway-v1")
        self.assertEqual(offers, [OFFER])
        messages = ["", "{}", '{"jsonrpc":"2.0","method":"ping"}', "blåbærgrød ✓ 🚀 中文"]
        for message in messages:
            client.send_text(message)
            self.assertEqual(server.recv_message(), message)
            server.send_text(message)
            self.assertEqual(client.recv_message(), message)
        client.send_binary(b"\x00\xffbinary")
        self.assertEqual(server.recv_message(), b"\x00\xffbinary")
        client.send_text("utf-8 bytes ✓".encode())
        self.assertEqual(server.recv_message(), "utf-8 bytes ✓")

    def test_payload_length_boundaries_in_both_directions(self):
        server, client = self.open_pair()
        with ThreadPoolExecutor(1) as pool:
            for size in (0, 1, 125, 126, 127, 65535, 65536, 70 * 1024):
                data = bytes(range(256)) * (size // 256) + bytes(size % 256)
                with self.subTest(size=size):
                    received = pool.submit(server.recv_message)
                    client.send_binary(data)
                    self.assertEqual(received.result(TIMEOUT), data)
                    received = pool.submit(client.recv_message)
                    server.send_binary(data)
                    self.assertEqual(received.result(TIMEOUT), data)

    def test_length_encodings_and_masking_direction(self):
        server, raw = self.raw_server()
        for size, indicator in ((125, 125), (126, 126), (65535, 126), (65536, 127)):
            with self.subTest(size=size):
                server.send_binary(b"s" * size)
                sent = read_frame(raw)
                self.assertEqual((sent.indicator, sent.masked, sent.opcode), (indicator, False, BINARY))
                self.assertEqual(sent.payload, b"s" * size)
        client, raw = self.raw_client()
        client.send_text("masked ✓")
        sent = read_frame(raw)
        self.assertEqual((sent.fin, sent.opcode, sent.masked), (True, TEXT, True))
        self.assertEqual(sent.payload, "masked ✓".encode())

    def test_large_text_messages(self):
        server, client = self.open_pair()
        message = ('{"history":"' + "blåbær ✓ " * (17 * 1024 * 1024 // 12) + '"}')
        self.assertGreater(len(message.encode()), 16 * 1024 * 1024)
        with ThreadPoolExecutor(1) as pool:
            received = pool.submit(server.recv_message)
            client.send_text(message)
            self.assertEqual(received.result(TIMEOUT), message)
            received = pool.submit(client.recv_message)
            server.send_text(message)
            self.assertEqual(received.result(TIMEOUT), message)

    def test_fragmented_message_is_reassembled_around_control_frames(self):
        server, raw = self.raw_server()
        text = "Hello, wörld ✓".encode()
        split = text.index("ö".encode()) + 1  # Split inside a multi-byte character.
        raw.sendall(frame(TEXT, text[:split], fin=False) + frame(PING, b"mid")
                    + frame(0x0, text[split:-2], fin=False) + frame(0x0, text[-2:]))
        self.assertEqual(server.recv_message(), "Hello, wörld ✓")
        self.assertEqual(read_frame(raw)[:3], (True, PONG, False))
        raw.sendall(frame(BINARY, b"\x01", fin=False) + frame(0x0, b"\x02"))
        self.assertEqual(server.recv_message(), b"\x01\x02")

    def test_ping_is_answered_with_matching_pong(self):
        server, raw = self.raw_server()
        raw.sendall(frame(PING, b"hello") + frame(TEXT, b"next"))
        self.assertEqual(server.recv_message(), "next")
        self.assertEqual(read_frame(raw), (True, PONG, False, 5, b"hello"))

    def test_ping_and_pong_callback_between_endpoints(self):
        pongs = []
        server, client = self.open_pair(on_pong=pongs.append)
        with ThreadPoolExecutor(1) as pool:
            received = pool.submit(server.recv_message)
            client.ping(b"heartbeat")
            client.send_text("after ping")
            self.assertEqual(received.result(TIMEOUT), "after ping")
        server.send_text("reply")
        self.assertEqual(client.recv_message(), "reply")
        self.assertEqual(pongs, [b"heartbeat"])
        with self.assertRaises(ValueError):
            client.ping(b"x" * 126)

    def test_concurrent_senders_do_not_interleave(self):
        server, client = self.open_pair()
        messages = {f"{sender}:{index}:" + "x" * (index * 1999 % 90000)
                    for sender in range(4) for index in range(40)}

        def send(sender):
            for message in sorted(messages):
                if message.startswith(f"{sender}:"):
                    client.send_text(message)

        with ThreadPoolExecutor(5) as pool:
            received = pool.submit(lambda: {server.recv_message() for _ in messages})
            for sending in [pool.submit(send, sender) for sender in range(4)]:
                sending.result(TIMEOUT)
            self.assertEqual(received.result(TIMEOUT), messages)

    def test_send_text_rejects_invalid_utf8_bytes(self):
        client, raw = self.raw_client()
        with self.assertRaises(UnicodeDecodeError):
            client.send_text(b"\xff")


class CloseTests(SocketTestCase):
    def test_close_handshake_between_endpoints(self):
        server, client = self.open_pair()
        client.close(4001, "bye ✓")
        with self.assertRaises(WebSocketClosed) as caught:
            server.recv_message()
        self.assertEqual((caught.exception.code, caught.exception.reason), (4001, "bye ✓"))
        with self.assertRaises(WebSocketClosed) as caught:
            client.recv_message()
        self.assertEqual(caught.exception.code, 4001)
        for endpoint in (server, client):
            with self.assertRaises(WebSocketClosed):
                endpoint.send_text("late")
            with self.assertRaises(WebSocketClosed):
                endpoint.recv_message()

    def test_peer_close_is_echoed_once(self):
        sock, raw = self.pair()
        server = WebSocket(sock, is_client=False)
        raw.sendall(frame(CLOSE, close_payload(1001, "going ✓".encode())))
        with self.assertRaises(WebSocketClosed) as caught:
            server.recv_message()
        self.assertEqual((caught.exception.code, caught.exception.reason), (1001, "going ✓"))
        self.assertEqual(read_frame(raw), (True, CLOSE, False, 2, close_payload(1001)))
        server.close()  # Already sent: a no-op.
        sock.close()
        self.assert_eof(raw)

    def test_close_without_status(self):
        server, raw = self.raw_server()
        raw.sendall(frame(CLOSE))
        with self.assertRaises(WebSocketClosed) as caught:
            server.recv_message()
        self.assertEqual((caught.exception.code, caught.exception.reason), (1005, ""))
        self.assertEqual(read_frame(raw).payload, b"")

    def test_local_close_still_delivers_messages_until_peer_replies(self):
        sock, raw = self.pair()
        server = WebSocket(sock, is_client=False)
        server.close(4000, "done")
        self.assertEqual(read_frame(raw), (True, CLOSE, False, 6, close_payload(4000, b"done")))
        raw.sendall(frame(TEXT, b"in flight") + frame(PING)
                    + frame(CLOSE, close_payload(4000, b"ack")))
        self.assertEqual(server.recv_message(), "in flight")
        with self.assertRaises(WebSocketClosed) as caught:
            server.recv_message()
        self.assertEqual((caught.exception.code, caught.exception.reason), (4000, "ack"))
        sock.close()
        self.assert_eof(raw)  # Neither a pong nor a second close frame was sent.

    def test_connection_end_without_close_frame(self):
        server, raw = self.raw_server()
        raw.sendall(frame(TEXT, b"partial", fin=False)[:5])
        raw.close()
        with self.assertRaises(WebSocketClosed) as caught:
            server.recv_message()
        self.assertEqual(caught.exception.code, 1006)

    def test_close_arguments_are_validated(self):
        client, _ = self.raw_client()
        for code in (999, 1004, 1005, 1006, 1015, 2999, 5000):
            with self.subTest(code=code), self.assertRaises(ValueError):
                client.close(code)
        with self.assertRaises(ValueError):
            client.close(1000, "x" * 124)


class ProtocolViolationTests(SocketTestCase):
    def assert_failed(self, endpoint: WebSocket, raw: socket.socket, code: int, masked: bool) -> None:
        with self.assertRaises(WebSocketError) as caught:
            endpoint.recv_message()
        self.assertNotIsInstance(caught.exception, WebSocketClosed)
        self.assertEqual(caught.exception.code, code)
        sent = read_frame(raw)
        self.assertEqual((sent.opcode, sent.masked), (CLOSE, masked))
        self.assertEqual(struct.unpack("!H", sent.payload[:2])[0], code)
        with self.assertRaises(WebSocketError) as again:
            endpoint.recv_message()
        self.assertEqual(again.exception.code, code)
        with self.assertRaises(WebSocketClosed):
            endpoint.send_text("after failure")

    def test_server_rejects_protocol_violations(self):
        cases = [
            ("unmasked client frame", frame(TEXT, b"hi", key=None), 1002),
            ("reserved bit", frame(TEXT, b"hi", rsv=0x40), 1002),
            ("fragmented control frame", frame(PING, b"", fin=False), 1002),
            ("oversized control frame", frame(PING, b"x" * 126), 1002),
            ("unknown opcode", frame(0x3, b"x"), 1002),
            ("unexpected continuation", frame(0x0, b"x"), 1002),
            ("interleaved data frame", frame(TEXT, b"a", fin=False) + frame(TEXT, b"b"), 1002),
            ("63-bit length", bytes([0x82, 0xFF]) + struct.pack("!Q", 1 << 63) + KEY, 1002),
            ("one-byte close", frame(CLOSE, b"\x03"), 1002),
            ("reserved close code", frame(CLOSE, close_payload(1005)), 1002),
            ("out-of-range close code", frame(CLOSE, close_payload(999)), 1002),
            ("invalid UTF-8 text", frame(TEXT, b"ok \xff\xfe"), 1007),
            ("invalid UTF-8 across fragments",
             frame(TEXT, b"\xe2\x82", fin=False) + frame(0x0, b"("), 1007),
            ("invalid UTF-8 close reason", frame(CLOSE, close_payload(1000, b"\xff")), 1007),
            # Rejected from the header alone, before any payload is awaited.
            ("default size limit", frame(TEXT, length=256 * 1024 * 1024 + 1), 1009),
            ("declared huge frame", frame(BINARY, length=1 << 40), 1009),
        ]
        for name, data, code in cases:
            with self.subTest(name):
                server, raw = self.raw_server()
                raw.sendall(data)
                self.assert_failed(server, raw, code, masked=False)

    def test_size_limit_applies_to_frames_and_fragments(self):
        for data in (frame(TEXT, b"x" * 1025), frame(TEXT, b"x" * 600, fin=False) + frame(0x0, b"x" * 600)):
            server, raw = self.raw_server(max_message_size=1024)
            raw.sendall(data)
            self.assert_failed(server, raw, 1009, masked=False)
        server, raw = self.raw_server(max_message_size=1024)
        raw.sendall(frame(TEXT, b"x" * 1024))
        self.assertEqual(server.recv_message(), "x" * 1024)

    def test_client_rejects_masked_server_frames(self):
        client, raw = self.raw_client()
        raw.sendall(frame(TEXT, b"masked"))
        self.assert_failed(client, raw, 1002, masked=True)

    def test_client_rejects_reserved_bits(self):
        client, raw = self.raw_client()
        raw.sendall(frame(TEXT, b"x", key=None, rsv=0x10))
        self.assert_failed(client, raw, 1002, masked=True)


if __name__ == "__main__":
    unittest.main()
