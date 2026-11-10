import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from hermachy import __version__
from hermachy.bridge import Bindings
from hermachy.protocol import FORCED_COMMAND, LaunchRequest, encode_request
from hermachy.server import (
    ServerConfig, ServerError, build_executor_ssh_argv, load_server_config, main, plugin_enabled,
    resolve_session, run_doctor,
)


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="hermachy-server-"))
        self.hermes = self.temp / "hermes"
        self.hermes.write_text("#!/bin/sh\necho 'Hermes Agent v0.23.1'\n")
        self.hermes.chmod(0o755)
        self.key = self.temp / "key"
        self.key.write_text("k")
        self.key.chmod(0o600)
        self.home = self.temp / "hermes-home"
        (self.home / "plugins" / "hermachy").mkdir(parents=True)
        (self.home / "plugins" / "hermachy" / "plugin.yaml").write_text(f"name: hermachy\nversion: {__version__}\n")
        (self.home / "config.yaml").write_text("terminal:\n  backend: local\nplugins:\n  enabled:\n    - hermachy\n")
        self.path = self.temp / "server.json"
        self.write({})

    def write(self, overrides: dict, mode: int = 0o600) -> None:
        payload = {"hermes_command": str(self.hermes), "hermes_home": str(self.home),
                   "executor_host": "omarchy", "executor_user": "omarchy-user",
                   "executor_identity_file": str(self.key), "executor_port": 22}
        payload.update(overrides)
        payload = {k: v for k, v in payload.items() if v is not None}
        self.path.write_text(json.dumps(payload))
        self.path.chmod(mode)

    def test_loads_private_config(self) -> None:
        config = load_server_config(self.path)
        self.assertEqual(config.hermes_command, self.hermes)
        self.assertEqual(config.hermes_home, self.home)
        self.assertEqual(config.executor_port, 22)

    def test_rejects_config_with_group_or_other_permissions(self) -> None:
        self.write({}, mode=0o640)
        with self.assertRaises(ServerError):
            load_server_config(self.path)

    def test_rejects_missing_unknown_invalid_and_0_5_config(self) -> None:
        for overrides in ({"hermes_command": None}, {"extra": 1}, {"executor_port": 0},
                          {"executor_host": "two words"}, {"hermes_command": "relative"},
                          {"hermes_home": str(self.temp / "missing")},
                          {"hermes_source_root": "/opt/hermes"}):
            with self.subTest(overrides=overrides):
                self.write(overrides)
                with self.assertRaises(ServerError):
                    load_server_config(self.path)

    def test_plugin_enabled_reads_block_and_inline_lists(self) -> None:
        config = load_server_config(self.path)
        self.assertTrue(plugin_enabled(config))
        (self.home / "config.yaml").write_text("plugins:\n  enabled: [other, hermachy]\n")
        self.assertTrue(plugin_enabled(config))
        (self.home / "config.yaml").write_text("plugins:\n  enabled:\n    - other\nterminal:\n  - hermachy\n")
        self.assertFalse(plugin_enabled(config))

    def test_executor_ssh_argv_is_fixed_and_quotes_no_client_shell(self) -> None:
        config = ServerConfig(Path("/h"), "host", "user", Path("/key"), 2200)
        argv = build_executor_ssh_argv(config, "/home/u/a; touch /tmp/pwned")
        self.assertEqual(argv[:4], ["ssh", "-T", "-F", "/dev/null"])
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertEqual(argv[-2], "user@host")
        self.assertNotIn("touch /tmp/pwned", argv[-1])

    loaded_version = __version__

    def doctor(self, executor_report: dict, *, plugin_version: str = __version__):
        (self.home / "plugins" / "hermachy" / "plugin.yaml").write_text(f"version: {plugin_version}\n")
        config = load_server_config(self.path)
        output = io.StringIO()
        loaded = {"plugins": [{"name": "hermachy", "version": self.loaded_version, "enabled": True, "error": ""}]}
        with mock.patch("hermachy.bridge.request_once", return_value=loaded), \
                mock.patch("hermachy.server.subprocess.run", side_effect=[
                    subprocess.CompletedProcess([], 0, "Hermes Agent v0.23.1\n", ""),
                    subprocess.CompletedProcess([], 0, json.dumps(executor_report), "")]), \
                mock.patch("sys.stdout", output):
            code = run_doctor(config, LaunchRequest("/p", "doctor"))
        return code, json.loads(output.getvalue())

    def test_doctor_reports_both_sides_and_the_shared_gateway_transport(self) -> None:
        access = {"version": "1", "ssh_entrypoint": True, "desktop": True, "wayland": True, "user_bus": True,
                  "administration": {"available": True}}
        code, report = self.doctor({"hostname": "o", "user": "omarchy-user", "cwd": "/p", "system": "Linux",
                                    "access": access})
        self.assertEqual(code, 0, report)
        self.assertEqual(report["transport"], "shared-gateway-v1")
        self.assertEqual(report["version"], __version__)
        self.assertEqual(report["studio"]["plugin"], __version__)
        self.assertEqual(report["warnings"], [])

    def test_doctor_refuses_a_stale_plugin(self) -> None:
        code, report = self.doctor({"hostname": "o", "user": "omarchy-user", "cwd": "/p", "system": "Linux"},
                                   plugin_version="0.5.2")
        self.assertEqual(code, 1)
        self.assertIn("plugin", report["error"])

    def test_doctor_refuses_when_the_running_gateway_has_not_loaded_the_plugin(self) -> None:
        self.loaded_version = "0.5.2"
        code, report = self.doctor({"hostname": "o", "user": "omarchy-user", "cwd": "/p", "system": "Linux"})
        self.assertEqual(code, 1)
        self.assertIn("gateway", report["error"])

    def test_doctor_rejects_mismatched_executor_identity(self) -> None:
        for executor in ({"hostname": "o", "user": "other", "cwd": "/p", "system": "Linux"},
                         {"hostname": "o", "user": "omarchy-user", "cwd": "/wrong", "system": "Linux"},
                         {"hostname": "o", "user": "omarchy-user", "cwd": "/p", "system": "Darwin"}):
            with self.subTest(executor=executor):
                code, report = self.doctor(executor)
                self.assertEqual(code, 1)
                self.assertEqual(report["status"], "error")

    def test_continue_resolves_the_latest_session_bound_to_this_workspace(self) -> None:
        bindings = Bindings(self.home)
        bindings.prepare("a" * 32, "/home/u/project")
        bindings.prepare("b" * 32, "/home/u/other")
        bindings.bind_session("older", "a" * 32)
        bindings.bind_session("elsewhere", "b" * 32)
        os.utime(self.home / "hermachy" / "sessions" / "older.json", (1, 1))
        bindings.bind_session("newer", "a" * 32)
        config = load_server_config(self.path)
        output = io.StringIO()
        with mock.patch("sys.stdout", output), mock.patch("hermachy.bridge.request_once") as remote:
            self.assertEqual(resolve_session(config, LaunchRequest("/home/u/project", "resolve")), 0)
            remote.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()), {"session": "newer"})

    def test_main_requires_the_v2_forced_command(self) -> None:
        with mock.patch.dict(os.environ, {"HERMACHY_SERVER_CONFIG": str(self.path),
                                          "SSH_ORIGINAL_COMMAND": "hermachy-v1 abc"}):
            self.assertEqual(main([]), 2)

    def test_main_runs_the_bridge_for_attach(self) -> None:
        request = LaunchRequest("/home/u/p", "attach", connection="c" * 32)
        with mock.patch.dict(os.environ, {"HERMACHY_SERVER_CONFIG": str(self.path),
                                          "SSH_ORIGINAL_COMMAND": f"{FORCED_COMMAND} {encode_request(request)}"}), \
                mock.patch("hermachy.bridge.run_attach", return_value=0) as attach:
            self.assertEqual(main([]), 0)
        args = attach.call_args.args
        self.assertEqual(args[:4], (self.hermes, self.home, "c" * 32, "/home/u/p"))

    def test_main_rejects_command_line_arguments(self) -> None:
        self.assertEqual(main(["--anything"]), 2)


if __name__ == "__main__":
    unittest.main()
