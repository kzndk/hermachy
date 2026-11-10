"""End-to-end attach without SSH: stand-in TUI -> relay -> forced-command server -> fake gateway."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlsplit

from hermachy import __version__, attach_policy
from hermachy.protocol import FORCED_COMMAND, encode_request
from hermachy import relay as relay_module
from hermachy.relay import Relay
from hermachy.websocket import WebSocket, WebSocketClosed, client_handshake, server_handshake

SRC = str(Path(__file__).resolve().parents[1] / "src")


class FakeGateway:
    """Accepts ticketed dials like the Hermes gateway and answers a few canonical methods."""

    def __init__(self, plugin_version=__version__):
        self.plugin_version = plugin_version
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.received: list[dict] = []
        self.dials = 0
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        def select(offered):
            assert "hermes-gateway-v1" in offered and any(p.startswith("hermes-gateway-ticket.") for p in offered)
            return "hermes-gateway-v1"
        conn.settimeout(10)
        server_handshake(conn, expected_path="/api/ws", select_subprotocol=select)
        conn.settimeout(None)
        self.dials += 1
        ws = WebSocket(conn, is_client=False)
        ws.send_text(json.dumps({"jsonrpc": "2.0", "method": "event",
                                 "params": {"type": "gateway.ready", "payload": {"session_authority": True}}}))
        try:
            while True:
                frame = json.loads(ws.recv_message())
                self.received.append(frame)
                method, params = frame.get("method"), frame.get("params") or {}
                if method == "plugins.list":
                    plugins = [] if self.plugin_version is None else [
                        {"name": "hermachy", "version": self.plugin_version, "enabled": True, "error": ""}]
                    result = {"plugins": plugins, "toolsets": []}
                elif method == "session.create":
                    result = {"session_id": f"S-{len(self.received)}", "stored_session_id": f"S-{len(self.received)}",
                              "message_count": 0, "messages": [], "info": {}, "echo": params}
                elif method == "session.resume":
                    result = {"session_id": params.get("session_id"), "message_count": 0, "messages": [], "info": {}}
                else:
                    result = {"method": method, "echo": params}
                ws.send_text(json.dumps({"jsonrpc": "2.0", "id": frame.get("id"), "result": result}))
        except (WebSocketClosed, OSError, ValueError):
            conn.close()


class AttachEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="hermachy-attach-"))
        self.gateway = FakeGateway()
        hermes = self.temp / "hermes"
        grant = {"url": f"ws://127.0.0.1:{self.gateway.port}/api/ws", "protocol": "hermes-gateway-v1",
                 "protocols": ["hermes-gateway-v1", "hermes-gateway-ticket.TEST"], "ticket": "TEST",
                 "endpoint": {"profile_id": "default", "instance_id": "i-1"}}
        hermes.write_text("#!/bin/sh\n"
                          'case "$*" in *"gateway attach"*) echo \'' + json.dumps(grant) + "' ;; "
                          '*--version*) echo "Hermes Agent v0.23.1" ;; *) exit 2 ;; esac\n')
        hermes.chmod(0o755)
        key = self.temp / "key"
        key.write_text("k")
        key.chmod(0o600)
        self.home = self.temp / "hermes-home"
        self.home.mkdir()
        self.config = self.temp / "server.json"
        self.config.write_text(json.dumps({"hermes_command": str(hermes), "hermes_home": str(self.home),
                                           "executor_host": "omarchy", "executor_user": "u",
                                           "executor_identity_file": str(key)}))
        self.config.chmod(0o600)
        self.workstation_cwd = "/home/u/project"
        self.connection = "c" * 32

        def ssh_argv(request):
            return ["env", f"SSH_ORIGINAL_COMMAND={FORCED_COMMAND} {encode_request(request)}",
                    f"HERMACHY_SERVER_CONFIG={self.config}", sys.executable, "-c",
                    f"import sys; sys.path.insert(0, {SRC!r}); from hermachy.server import main; raise SystemExit(main())"]

        self.saved_log, relay_module.LOG_PATH = relay_module.LOG_PATH, self.temp / "relay.log"
        self.relay = Relay(ssh_argv, self.workstation_cwd, self.connection, reasoning="high").start()

    def tearDown(self):
        self.relay.close()
        self.gateway.sock.close()
        relay_module.LOG_PATH = self.saved_log

    def tui(self):
        url = urlsplit(self.relay.url)
        sock = socket.create_connection((url.hostname, url.port), timeout=20)
        self.assertIsNone(client_handshake(sock, host=url.hostname, port=url.port, path=url.path, subprotocols=[]))
        ws = WebSocket(sock, is_client=True)
        ready = json.loads(ws.recv_message())
        self.assertEqual(ready["params"]["type"], "gateway.ready")
        return ws, sock

    def rpc(self, ws, request_id, method, params):
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}))
        while True:
            frame = json.loads(ws.recv_message())
            if frame.get("id") == request_id:
                return frame

    def test_session_create_is_bound_and_rewritten(self):
        ws, sock = self.tui()
        try:
            answer = self.rpc(ws, 1, "session.create", {"cwd": "/home/u/project", "client_env": {"PATH": "/x"},
                                                        "model": "m"})
        finally:
            ws.close()
            sock.close()
        echo = answer["result"]["echo"]
        workspace = self.home / "hermachy" / "workspaces" / self.connection
        self.assertEqual(echo["cwd"], str(workspace))
        self.assertTrue(workspace.is_dir())
        self.assertEqual(oct(workspace.stat().st_mode & 0o777), "0o700")
        self.assertNotIn("client_env", echo)
        self.assertEqual(echo["client"]["name"], "hermachy")
        self.assertEqual(echo["reasoning"], "high")
        self.assertEqual(echo["model"], "m")
        sid = answer["result"]["session_id"]
        binding = json.loads((self.home / "hermachy" / "sessions" / f"{sid}.json").read_text())
        self.assertEqual(binding, {"v": 1, "connection": self.connection})
        record = json.loads((self.home / "hermachy" / "connections" / f"{self.connection}.json").read_text())
        self.assertEqual(record["cwd"], self.workstation_cwd)
        self.assertEqual(oct((self.home / "hermachy" / "sessions" / f"{sid}.json").stat().st_mode & 0o777), "0o600")

    def test_refused_frames_never_reach_the_gateway(self):
        ws, sock = self.tui()
        try:
            cases = [
                ("cli.exec", {"command": "id"}),
                ("image.render", {"path": "/etc/passwd"}),
                ("vault.preview.fill", {"handle": "h", "target": "t"}),
                ("profiles.list", {}),
                ("session.create", {"safe_mode": True, "model": "m"}),
                ("session.create", {"ignore_user_config": "1", "model": "m"}),
                ("session.create", {"cwd_explicit": True}),
                ("session.mutate", {"operation": "workspace", "cwd": "/tmp"}),
                ("session.status", {"profile": "other"}),
                ("worker.adopt", {}),
                ("made.up.method", {}),
            ]
            for index, (method, params) in enumerate(cases, start=10):
                with self.subTest(method=method, params=params):
                    answer = self.rpc(ws, index, method, params)
                    self.assertEqual(answer["error"]["code"], 4035)
            allowed = self.rpc(ws, 99, "session.list", {"limit": 1})
            self.assertEqual(allowed["result"]["method"], "session.list")
        finally:
            ws.close()
            sock.close()
        methods = [frame.get("method") for frame in self.gateway.received]
        self.assertEqual(methods, ["plugins.list", "session.list"])

    def test_toolsets_are_forwarded(self):
        ws, sock = self.tui()
        try:
            answer = self.rpc(ws, 6, "session.create", {"toolsets": ["terminal", "file"]})
        finally:
            ws.close()
            sock.close()
        self.assertEqual(answer["result"]["echo"]["toolsets"], ["terminal", "file"])

    def test_gateway_without_the_loaded_plugin_is_never_attached(self):
        for version in (None, "0.5.2"):
            with self.subTest(plugin=version):
                self.gateway.plugin_version = version
                self.gateway.received.clear()
                url = urlsplit(self.relay.url)
                sock = socket.create_connection((url.hostname, url.port), timeout=20)
                try:
                    client_handshake(sock, host=url.hostname, port=url.port, path=url.path, subprotocols=[])
                    ws = WebSocket(sock, is_client=True)
                    with self.assertRaises(WebSocketClosed):
                        while True:
                            frame = json.loads(ws.recv_message())
                            self.assertNotEqual(frame.get("params", {}).get("type"), "gateway.ready")
                finally:
                    sock.close()
                self.assertEqual([f.get("method") for f in self.gateway.received], ["plugins.list"])

    def test_resume_binds_the_resumed_chat_to_this_connection(self):
        ws, sock = self.tui()
        try:
            self.rpc(ws, 5, "session.resume", {"session_id": "older-chat"})
        finally:
            ws.close()
            sock.close()
        binding = json.loads((self.home / "hermachy" / "sessions" / "older-chat.json").read_text())
        self.assertEqual(binding["connection"], self.connection)

    def test_tui_reconnect_opens_a_fresh_channel(self):
        for attempt in range(2):
            ws, sock = self.tui()
            try:
                answer = self.rpc(ws, attempt, "ping", {})
                self.assertIn("result", answer)
            finally:
                ws.close()
                sock.close()
        self.assertEqual(self.gateway.dials, 2)

    def test_wrong_secret_path_is_refused(self):
        url = urlsplit(self.relay.url)
        sock = socket.create_connection((url.hostname, url.port), timeout=10)
        try:
            with self.assertRaises(Exception):
                client_handshake(sock, host=url.hostname, port=url.port, path="/guess", subprotocols=[])
        finally:
            sock.close()


class AttachPolicyTests(unittest.TestCase):
    def test_declared_methods_come_from_the_pinned_hermes(self):
        self.assertIn("session.create", attach_policy.DECLARED_METHODS)
        self.assertIn("runtime.describe", attach_policy.DECLARED_METHODS)
        self.assertIn("gateway.ping", attach_policy.DECLARED_METHODS)

    def test_denied_methods_are_declared_upstream_names(self):
        # A typo in the denial list would silently allow the real method.
        for method in attach_policy.DENIED_METHODS:
            with self.subTest(method=method):
                self.assertIn(method, attach_policy.DECLARED_METHODS)

    def test_gateway_requests_answered_by_the_client_pass_through(self):
        line = json.dumps({"jsonrpc": "2.0", "id": "approval-1", "result": {"choice": "once"}})
        self.assertEqual(attach_policy.client_frame(line, host_cwd="/h", client_version="0.6.0").frame, line)

    def test_answer_detection_only_for_the_pending_id(self):
        answer = json.dumps({"jsonrpc": "2.0", "id": 7, "result": {"session_id": "a", "stored_session_id": "b"}})
        self.assertEqual(attach_policy.answered_sessions(answer, 7), ["a", "b"])
        self.assertIsNone(attach_policy.answered_sessions(answer, 8))
        event = json.dumps({"jsonrpc": "2.0", "method": "event", "id": 7, "params": {}})
        self.assertIsNone(attach_policy.answered_sessions(event, 7))


if __name__ == "__main__":
    unittest.main()
