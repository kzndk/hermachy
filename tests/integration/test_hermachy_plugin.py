"""Hermachy plugin against a real Hermes runtime (v0.23.1+): loader, middleware chain, cache.

Run with Hermes's interpreter and source on the path, e.g.
``PYTHONPATH=src:<hermes-src> <hermes>/venv/bin/python -m unittest discover -s tests/integration``.
Only the SSH transport is replaced: commands of the workstation class run locally.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
PLUGIN_SOURCE = ROOT / "plugin" / "hermachy"


def _write_private(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(payload))
    path.chmod(0o600)


class HermachyPluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = Path(tempfile.mkdtemp(prefix="hermachy-plugin-"))
        cls.home = cls.temp / "hermes-home"
        (cls.home / "plugins").mkdir(parents=True)
        shutil.copytree(PLUGIN_SOURCE, cls.home / "plugins" / "hermachy")
        (cls.home / "config.yaml").write_text("terminal:\n  backend: local\nplugins:\n  enabled:\n    - hermachy\n")
        key = cls.temp / "executor_key"
        key.write_text("not a real key\n")
        key.chmod(0o600)
        cls.server_config = cls.temp / "server.json"
        _write_private(cls.server_config, {"executor_host": "omarchy.invalid", "executor_user": "tester",
                                           "executor_port": 22, "executor_identity_file": str(key)})
        os.environ["HERMES_HOME"] = str(cls.home)
        os.environ["HERMACHY_SERVER_CONFIG"] = str(cls.server_config)
        from hermes_cli.plugins import get_plugin_manager
        get_plugin_manager().discover_and_load()
        cls.plugin = next(m for n, m in sys.modules.items() if n.endswith("hermachy") and hasattr(m, "route_tool_call"))
        real = cls.plugin.workstation_environment_class()

        from tools.environments.base_output import _popen_bash

        class LoopbackWorkstationSSHEnvironment(real):
            """The real workstation class with its ssh transport replaced by local bash."""
            executed: list = []

            def _establish_connection(self):
                return None

            def _run_bash(self, cmd_string, *, login=False, timeout=120, stdin_data=None):
                LoopbackWorkstationSSHEnvironment.executed.append(cmd_string)
                return _popen_bash(["bash", "-c", cmd_string], stdin_data)

            def _shutdown_master(self):
                for remove in (self.control_socket.unlink, self.control_dir.rmdir):
                    try:
                        remove()
                    except OSError:
                        pass
                return True

        cls.fake = LoopbackWorkstationSSHEnvironment
        cls.plugin._WORKSTATION_CLASS = LoopbackWorkstationSSHEnvironment

    @classmethod
    def tearDownClass(cls):
        from tools.terminal_tool_lifecycle import cleanup_all_environments
        cleanup_all_environments()
        shutil.rmtree(cls.temp, ignore_errors=True)

    # helpers ---------------------------------------------------------------------------------
    def assertSameDirectory(self, printed: str, expected: Path) -> None:
        # macOS's /tmp and /var are symlinks: `pwd` prints the logical path.
        self.assertEqual(os.path.realpath(printed), os.path.realpath(expected))

    def bind(self, *, session_id: str | None = None) -> tuple[str, Path]:
        connection = uuid.uuid4().hex
        workstation = Path(tempfile.mkdtemp(dir=self.temp, prefix="workstation-"))
        hermachy = self.home / "hermachy"
        _write_private(hermachy / "connections" / f"{connection}.json",
                       {"v": 1, "connection": connection, "cwd": str(workstation)})
        (hermachy / "workspaces" / connection).mkdir(parents=True, exist_ok=True)
        if session_id:
            _write_private(hermachy / "sessions" / f"{session_id}.json", {"v": 1, "connection": connection})
        return connection, workstation

    def call(self, session_id: str, tool: str, args: dict, *, key: str | None = None, cwd: str | None = None):
        from model_tools import handle_function_call
        from gateway.session_context import scoped_session_key
        from agent.runtime_cwd import reset_session_cwd, set_session_cwd
        token = set_session_cwd(cwd) if cwd else None
        try:
            with scoped_session_key(key or f"tui:{session_id}"):
                return json.loads(handle_function_call(tool, args, task_id=f"task-{session_id}", session_id=session_id))
        finally:
            if token is not None:
                reset_session_cwd(token)

    def env_for(self, session_id: str, key: str | None = None):
        from gateway.session_context import scoped_session_key
        from tools.terminal_tool_lifecycle import get_active_env
        with scoped_session_key(key or f"tui:{session_id}"):
            return get_active_env(f"task-{session_id}")

    # routing -----------------------------------------------------------------------------------
    def test_unbound_session_is_untouched(self):
        sid = "unbound-" + uuid.uuid4().hex[:8]
        result = self.call(sid, "terminal", {"command": "echo host", "timeout": 20})
        self.assertEqual(result.get("exit_code"), 0, result)
        self.assertEqual(type(self.env_for(sid)).__name__, "LocalEnvironment")

    def test_bound_session_runs_in_workstation_environment_and_cwd(self):
        sid = "bound-" + uuid.uuid4().hex[:8]
        _, workstation = self.bind(session_id=sid)
        result = self.call(sid, "terminal", {"command": "pwd", "timeout": 20})
        self.assertEqual(result.get("exit_code"), 0, result)
        self.assertSameDirectory(result["output"].strip(), workstation)
        self.assertIsInstance(self.env_for(sid), self.fake)

    def test_workspace_cwd_marks_a_bridge_created_session(self):
        sid = "created-" + uuid.uuid4().hex[:8]
        connection, workstation = self.bind()
        marker = self.home / "hermachy" / "workspaces" / connection
        result = self.call(sid, "terminal", {"command": "pwd", "timeout": 20}, cwd=str(marker))
        self.assertSameDirectory(result["output"].strip(), workstation)

    def test_subagent_with_own_session_id_follows_the_parent_session_key(self):
        parent = "parent-" + uuid.uuid4().hex[:8]
        _, workstation = self.bind(session_id=parent)
        self.call(parent, "terminal", {"command": "true", "timeout": 20})
        child = self.call("child-" + uuid.uuid4().hex[:8], "terminal", {"command": "pwd", "timeout": 20},
                          key=f"tui:{parent}")
        self.assertSameDirectory(child["output"].strip(), workstation)

    def test_environment_cached_before_binding_is_replaced(self):
        sid = "late-" + uuid.uuid4().hex[:8]
        self.call(sid, "terminal", {"command": "true", "timeout": 20})
        self.assertEqual(type(self.env_for(sid)).__name__, "LocalEnvironment")
        _, workstation = self.bind(session_id=sid)
        result = self.call(sid, "terminal", {"command": "pwd", "timeout": 20})
        self.assertSameDirectory(result["output"].strip(), workstation)
        self.assertIsInstance(self.env_for(sid), self.fake)

    def test_workstation_environment_is_released_when_unbound(self):
        sid = "unbind-" + uuid.uuid4().hex[:8]
        self.bind(session_id=sid)
        self.call(sid, "terminal", {"command": "true", "timeout": 20})
        (self.home / "hermachy" / "sessions" / f"{sid}.json").unlink()
        with self.plugin._KEY_LOCK:
            self.plugin._KEY_ROUTES.pop(f"tui:{sid}", None)
        self.call(sid, "terminal", {"command": "true", "timeout": 20})
        self.assertEqual(type(self.env_for(sid)).__name__, "LocalEnvironment")

    def test_broken_binding_fails_closed_without_executing(self):
        sid = "broken-" + uuid.uuid4().hex[:8]
        _write_private(self.home / "hermachy" / "sessions" / f"{sid}.json", {"v": 1, "connection": "f" * 32})
        target = self.temp / f"must-not-exist-{sid}"
        result = self.call(sid, "terminal", {"command": f"touch {target}", "timeout": 20})
        self.assertEqual(result["status"], "error")
        self.assertIn("Nothing was executed", result["error"])
        self.assertFalse(target.exists())

    def test_internal_failure_is_an_error_result_not_a_skipped_middleware(self):
        sid = "failure-" + uuid.uuid4().hex[:8]
        self.bind(session_id=sid)
        original = self.plugin.load_executor
        self.plugin.load_executor = lambda: (_ for _ in ()).throw(ValueError("boom"))
        try:
            target = self.temp / f"must-not-exist-{sid}"
            result = self.call(sid, "terminal", {"command": f"touch {target}", "timeout": 20})
        finally:
            self.plugin.load_executor = original
        self.assertEqual(result["status"], "error")
        self.assertFalse(target.exists())

    def test_file_tools_follow_the_route_with_remote_path_resolution(self):
        sid = "files-" + uuid.uuid4().hex[:8]
        _, workstation = self.bind(session_id=sid)
        (workstation / "marker.txt").write_text("workstation file\n")
        result = self.call(sid, "read_file", {"path": "marker.txt"})
        self.assertIn("workstation file", json.dumps(result))
        from gateway.session_context import scoped_session_key
        from tools.file_tools_paths import _terminal_env_type_for_task
        with scoped_session_key(f"tui:{sid}"):
            self.assertEqual(_terminal_env_type_for_task(f"task-{sid}"), "ssh")

    # environment ---------------------------------------------------------------------------------
    def test_ssh_command_uses_only_the_dedicated_identity_and_strict_host_keys(self):
        _, workstation = self.bind()
        route = self.plugin.Route("a" * 32, str(workstation), self.plugin.load_executor())
        env = self.fake(route, 20)
        try:
            argv = env._build_ssh_command()
            joined = " ".join(argv)
            for option in ("IdentitiesOnly=yes", "IdentityAgent=none", "StrictHostKeyChecking=yes",
                           "ForwardAgent=no", "ClearAllForwardings=yes", "BatchMode=yes"):
                self.assertIn(option, joined)
            self.assertEqual(argv[:2], ["ssh", "-T"])
            self.assertIn(f"ControlPath={env.control_socket}", joined)
            self.assertTrue(str(env.control_dir).startswith("/tmp/hermachy-ssh-"))
            self.assertEqual(os.stat(env.control_dir).st_mode & 0o777, 0o700)
            self.assertNotIn("SendEnv", joined)
            self.assertEqual(argv[argv.index("-i") + 1], route.executor.key)
            self.assertIsNone(env._sync_manager)
            self.assertEqual(env._prepare_command("sudo id"), ("sudo id", None))
            with self.assertRaises(RuntimeError):
                env._scp_upload("a", "b")
        finally:
            env.cleanup()

    def test_execution_exception_is_an_uncertain_outcome_not_a_retry(self):
        _, workstation = self.bind()
        route = self.plugin.Route("b" * 32, str(workstation), self.plugin.load_executor())
        env = self.fake(route, 20)
        calls = []

        def lost(*_args, **_kwargs):
            calls.append(1)
            raise ConnectionError("link lost")
        from unittest import mock
        from tools.environments.ssh import SSHEnvironment
        try:
            with mock.patch.object(SSHEnvironment, "execute", lost):
                result = env.execute("echo once")
        finally:
            env.cleanup()
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["returncode"], 255)
        self.assertTrue(result["hermachy_outcome_unknown"])

    def test_provider_refuses_outside_a_bound_call(self):
        provider = self.plugin._provider_class()()
        with self.assertRaises(RuntimeError):
            provider.create_environment(cwd="/", timeout=10)
        self.assertFalse(provider.is_container)
        self.assertFalse(provider.skip_container_guards)

    def test_server_config_must_be_private(self):
        self.server_config.chmod(0o640)
        try:
            with self.assertRaises(self.plugin.RouteError):
                self.plugin.load_executor()
        finally:
            self.server_config.chmod(0o600)

    def test_legacy_terminal_target_is_honoured_in_bound_sessions(self):
        sid = "legacy-" + uuid.uuid4().hex[:8]
        _, workstation = self.bind(session_id=sid)
        before = len(self.fake.executed)
        host = self.call(sid, "terminal", {"command": "echo on-host", "target": "hermes", "workdir": str(self.temp)})
        self.assertEqual(host.get("target"), "hermes", host)
        self.assertEqual(host.get("exit_code"), 0, host)
        self.assertEqual(len(self.fake.executed), before, "a hermes-target command reached the workstation")
        remote = self.call(sid, "terminal", {"command": "pwd", "target": "omarchy", "timeout": 20})
        self.assertSameDirectory(remote["output"].strip(), workstation)
        for args in ({"command": "true", "target": "mars"},
                     {"command": "sleep 1", "target": "hermes", "background": True}):
            with self.subTest(args=args):
                before = len(self.fake.executed)
                refused = self.call(sid, "terminal", args)
                self.assertEqual(refused["status"], "error")
                self.assertEqual(len(self.fake.executed), before)

    # host tool and prompt ------------------------------------------------------------------------
    def test_host_tool_refuses_in_unbound_sessions(self):
        result = self.call("plain-" + uuid.uuid4().hex[:8], "hermes_host", {"command": "true"})
        self.assertEqual(result["status"], "error")

    def test_host_tool_runs_on_the_host_in_bound_sessions(self):
        sid = "host-" + uuid.uuid4().hex[:8]
        self.bind(session_id=sid)
        before = len(self.fake.executed)
        result = self.call(sid, "hermes_host", {"command": "echo on-host", "workdir": str(self.temp)})
        self.assertEqual(result.get("exit_code"), 0, result)
        self.assertEqual(result["target"], "hermes")
        self.assertIn("on-host", result["output"])
        self.assertEqual(len(self.fake.executed), before)

    def test_prompt_section_only_for_bound_sessions(self):
        self.assertEqual(self.plugin.prompt_section({"session_id": "nobody", "cwd": "/"}), "")
        sid = "prompt-" + uuid.uuid4().hex[:8]
        _, workstation = self.bind(session_id=sid)
        (workstation / "AGENTS.md").write_text("Use tabs.\n")
        text = self.plugin.prompt_section({"session_id": sid, "cwd": ""})
        self.assertIn("Hermachy workstation chat", text)
        self.assertIn(str(workstation), text)
        self.assertLessEqual(len(text), 4000)


if __name__ == "__main__":
    unittest.main()
