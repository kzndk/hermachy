import json
from pathlib import Path
from types import ModuleType
import unittest
from unittest import mock

from hermachy.runtime import execute, route_terminal, workstation_environment


class ExecutionTests(unittest.TestCase):
    def test_executor_exception_becomes_uncertain_result_without_retry(self):
        class Backend:
            calls = 0

            def execute(self, command, **kwargs):
                self.calls += 1
                raise OSError("connection lost after starting the command")

        environment = workstation_environment(Backend)()
        result = environment.execute("change the machine", timeout=30)
        self.assertEqual(environment.calls, 1)
        self.assertEqual(result["returncode"], 255)
        self.assertTrue(result["hermachy_outcome_unknown"])
        self.assertIn("do not rerun", result["output"])

    def test_executor_keeps_current_desktop_and_never_collects_sudo_passwords(self):
        class Backend:
            def _snapshot_excluded_passthrough_names(self):
                return ("SESSION_FIELD",)

            def _prepare_command(self, command):
                raise AssertionError("password handling must not run on Hermes")

        environment = workstation_environment(Backend)()
        names = environment._snapshot_excluded_passthrough_names()
        self.assertIn("SESSION_FIELD", names)
        self.assertIn("HYPRLAND_INSTANCE_SIGNATURE", names)
        self.assertIn("WAYLAND_DISPLAY", names)
        self.assertEqual(environment._prepare_command("sudo id"), ("sudo id", None))

    def test_executor_preserves_normal_failures_and_marks_ssh_loss_or_timeout(self):
        class Backend:
            def execute(self, command):
                return {"returncode": int(command), "output": "partial output"}

        environment = workstation_environment(Backend)()
        for code in (0, 1, 124, 255):
            result = environment.execute(str(code))
            self.assertEqual(result["returncode"], code)
            self.assertEqual(bool(result.get("hermachy_outcome_unknown")), code in (124, 255))

    def modules(self, *, approved=True):
        terminal = ModuleType("tools.terminal_tool")
        terminal.terminal_tool = mock.Mock(return_value='{"exit_code":0,"output":"omarchy"}')
        terminal._handle_terminal = mock.Mock(return_value='{"exit_code":0,"output":"omarchy"}')
        terminal._check_all_guards = mock.Mock(return_value={"approved": approved, "status": "blocked"})
        local = ModuleType("tools.environments.local")
        environment = mock.Mock()
        environment.execute.return_value = {"returncode": 0, "output": "hermes"}
        local.LocalEnvironment = mock.Mock(return_value=environment)
        package = ModuleType("tools")
        package.terminal_tool = terminal
        return {"tools": package, "tools.terminal_tool": terminal,
                "tools.environments.local": local}, terminal, local

    def test_explicit_targets_use_distinct_environments(self):
        modules, terminal, local = self.modules()
        with mock.patch.dict("sys.modules", modules):
            local_result = json.loads(execute({"target": "omarchy", "command": "pwd"}, task_id="test"))
            remote_result = json.loads(execute({"target": "hermes", "command": "pwd"}))
        self.assertEqual(local_result["output"], "omarchy")
        self.assertEqual(remote_result["output"], "hermes")
        terminal.terminal_tool.assert_called_once()
        terminal._check_all_guards.assert_called_once_with("pwd", "local")
        local.LocalEnvironment.assert_called_once_with(cwd=str(Path.home()), timeout=180)
        local.LocalEnvironment.return_value.cleanup.assert_called_once()

    def test_denial_never_creates_a_host_environment(self):
        modules, terminal, local = self.modules(approved=False)
        with mock.patch.dict("sys.modules", modules):
            result = json.loads(execute({"target": "hermes", "command": "blocked command"}))
        self.assertEqual(result["status"], "blocked")
        local.LocalEnvironment.assert_not_called()
        terminal.terminal_tool.assert_not_called()

    def test_native_terminal_preserves_background_and_defaults_to_omarchy(self):
        modules, terminal, local = self.modules()
        args = {"command": "tests", "background": True, "notify": True}
        with mock.patch.dict("sys.modules", modules):
            result = json.loads(route_terminal(args, task_id="test"))
        self.assertEqual(result["target"], "omarchy")
        terminal._handle_terminal.assert_called_once_with(args, task_id="test")
        local.LocalEnvironment.assert_not_called()

    def test_native_terminal_remote_command_and_unsupported_background(self):
        modules, terminal, local = self.modules()
        with mock.patch.dict("sys.modules", modules):
            result = json.loads(route_terminal({"target": "hermes", "command": "pwd"}))
            denied = json.loads(route_terminal({"target": "hermes", "command": "pwd", "background": True}))
        self.assertEqual(result["target"], "hermes")
        self.assertEqual(denied["status"], "error")
        terminal._handle_terminal.assert_not_called()
        local.LocalEnvironment.assert_called_once()

    def test_target_failure_does_not_fall_back_to_other_machine(self):
        modules, terminal, local = self.modules()
        terminal.terminal_tool.return_value = '{"exit_code":255,"error":"connection lost"}'
        with mock.patch.dict("sys.modules", modules):
            result = json.loads(execute({"target": "omarchy", "command": "build"}))
        self.assertEqual(result["exit_code"], 255)
        self.assertEqual(result["target"], "omarchy")
        local.LocalEnvironment.assert_not_called()

    def test_invalid_target_cwd_and_timeout_do_not_execute(self):
        modules, terminal, local = self.modules()
        for overrides in ({"target": "auto"}, {"target": []}, {"target": {}},
                          {"timeout": True}, {"timeout": 0},
                          {"timeout": 301}, {"workdir": "relative"}, {"background": True}):
            with self.subTest(overrides=overrides), mock.patch.dict("sys.modules", modules):
                result = json.loads(execute({"target": "hermes", "command": "pwd", **overrides}))
                self.assertEqual(result["status"], "error")
        terminal.terminal_tool.assert_not_called()
        local.LocalEnvironment.assert_not_called()
