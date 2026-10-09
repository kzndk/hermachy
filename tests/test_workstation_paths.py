import json
import shlex
import sys
from types import ModuleType
import unittest
from unittest import mock

from hermachy.workstation_paths import expand_tilde, install


class WorkstationPathTests(unittest.TestCase):
    def test_tilde_uses_protected_backend_and_preserves_literal_shell_characters(self):
        lifecycle = ModuleType("tools.terminal_tool_lifecycle")
        backend = mock.Mock(hermachy_safe_executor=True)
        lifecycle.ensure_task_env = mock.Mock(return_value=backend)
        path = "~/a;$(touch wrong machine)"
        backend.execute.return_value = {"returncode": 0, "output": json.dumps("/home/local/" + path[2:])}
        with mock.patch.dict(sys.modules, {"tools.terminal_tool_lifecycle": lifecycle}):
            self.assertEqual(expand_tilde(path), "/home/local/" + path[2:])
            self.assertEqual(expand_tilde("/tmp/example"), "/tmp/example")
        self.assertEqual(shlex.split(backend.execute.call_args.args[0])[-1], path)
        backend.execute.assert_called_once()

    def test_resolution_failure_never_falls_back_to_the_hermes_home(self):
        lifecycle = ModuleType("tools.terminal_tool_lifecycle")
        lifecycle.ensure_task_env = mock.Mock(return_value=None)
        with mock.patch.dict(sys.modules, {"tools.terminal_tool_lifecycle": lifecycle}), self.assertRaises(RuntimeError):
            expand_tilde("~/file")
        lifecycle.ensure_task_env.return_value = mock.Mock(hermachy_safe_executor=True)
        lifecycle.ensure_task_env.return_value.execute.return_value = {"returncode": 255, "output": "connection lost"}
        with mock.patch.dict(sys.modules, {"tools.terminal_tool_lifecycle": lifecycle}), self.assertRaises(RuntimeError):
            expand_tilde("~/file")

    def test_install_updates_already_imported_guard_references(self):
        package = ModuleType("tools")
        paths = ModuleType("tools.file_tools_paths")
        paths._expand_tilde = lambda path: "/Users/remote/" + path
        paths._uses_container_paths = lambda task_id: False
        guards = ModuleType("tools.file_tools_write_guards")
        guards._expand_tilde = paths._expand_tilde
        package.file_tools_paths = paths
        with mock.patch.dict(sys.modules, {"tools": package, "tools.file_tools_paths": paths,
                                          "tools.file_tools_write_guards": guards}):
            install()
        self.assertTrue(paths._uses_container_paths("session"))
        self.assertIs(paths._expand_tilde, expand_tilde)
        self.assertIs(guards._expand_tilde, expand_tilde)
