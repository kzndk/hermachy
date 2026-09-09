import json
from pathlib import Path
from types import ModuleType
import unittest
from unittest import mock

from hermachy.runtime import execute, route_terminal


class ExecutionTests(unittest.TestCase):
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
