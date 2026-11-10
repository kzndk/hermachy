"""Run with the pinned Hermes venv and both source roots on PYTHONPATH.

Only the workstation transport is substituted. Hermes builds the full prompt
and context listing with its real cwd scopes, caller signatures and imports.
"""
import json
import os
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from hermachy.context_files import collect_context_files
from hermachy.workspace_context import install


class HermesWorkspacePromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        profile = tempfile.TemporaryDirectory(prefix="hermachy-prompt-profile-")
        cls.addClassCleanup(profile.cleanup)
        cls.profile = Path(profile.name)
        (cls.profile / "SOUL.md").write_text("PROFILE_IDENTITY_SENTINEL")
        env = mock.patch.dict(os.environ, {"HERMES_HOME": profile.name,
                                          "HERMACHY_REQUIRE_SAFE_EXECUTOR": "0"})
        env.start()
        cls.addClassCleanup(env.stop)
        # Import the callers before installing the extension, as gateway startup can.
        from agent import system_prompt, context_file_sources, runtime_cwd
        from tools import terminal_scope
        cls.prompt, cls.sources, cls.cwd = system_prompt, context_file_sources, runtime_cwd
        cls.terminal_scope = terminal_scope
        install()

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="hermachy-prompt-workspaces-")
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        self.configured, self.session = root / "configured", root / "session"
        for path, text in ((self.configured, "CONFIGURED_WORKSPACE_SENTINEL"),
                           (self.session, "SESSION_WORKSPACE_SENTINEL")):
            path.mkdir()
            (path / "AGENTS.md").write_text(text)
        token = self.cwd.set_session_cwd(str(self.session))
        self.addCleanup(self.cwd.reset_session_cwd, token)
        token = self.terminal_scope.set_terminal_scope({"TERMINAL_CWD": str(self.configured),
                                                        "TERMINAL_ENV": "ssh"})
        self.addCleanup(self.terminal_scope.reset_terminal_scope, token)
        backend = mock.Mock(hermachy_safe_executor=True)
        backend.execute.side_effect = self.read_workstation
        patch = mock.patch("tools.terminal_tool_lifecycle.ensure_task_env", return_value=backend)
        self.environment = patch.start()
        self.addCleanup(patch.stop)
        self.agent = SimpleNamespace(
            load_soul_identity=True, skip_context_files=False, valid_tool_names=set(),
            _task_completion_guidance=False, _tool_use_enforcement=False,
            _environment_probe=False, _kanban_worker_guidance="", _memory_store=None,
            _memory_manager=None, model="gpt-6-astra-900k", provider="openai-codex",
            platform="tui", pass_session_id=False, session_id="",
            _session_db=SimpleNamespace(db_path=str(self.profile / "state.db")),
            _emit_diagnostic_status=lambda *_args, **_kwargs: None,
        )

    def read_workstation(self, command, timeout):
        argv = shlex.split(command)
        self.assertEqual(argv[:2], ["hermachy-exec", "--context-files"])
        return {"returncode": 0, "output": json.dumps(collect_context_files(argv[2]))}

    def check_prompt_and_listing(self, expected, excluded, path):
        prompt = self.prompt.build_system_prompt(self.agent)
        self.assertIn(expected, prompt)
        self.assertNotIn(excluded, prompt)
        self.assertEqual(prompt.count("PROFILE_IDENTITY_SENTINEL"), 1)
        files = self.sources.context_file_sources_for_agent(self.agent)
        loaded = {item["path"] for item in files if item["loaded"]}
        self.assertIn(str(path / "AGENTS.md"), loaded)
        self.assertIn(str(self.profile / "SOUL.md"), loaded)

    def test_full_prompt_and_context_listing_honor_session_override(self):
        self.check_prompt_and_listing("SESSION_WORKSPACE_SENTINEL", "CONFIGURED_WORKSPACE_SENTINEL",
                                      self.session)

    def test_launch_artifact_uses_configured_workspace_in_prompt_and_listing(self):
        self.agent._context_cwd_is_launch_artifact = True
        self.check_prompt_and_listing("CONFIGURED_WORKSPACE_SENTINEL", "SESSION_WORKSPACE_SENTINEL",
                                      self.configured)

    def test_launch_artifact_without_configured_workspace_fails_closed(self):
        self.agent._context_cwd_is_launch_artifact = True
        token = self.terminal_scope.set_terminal_scope({"TERMINAL_ENV": "ssh"})
        try:
            with self.assertRaises(ValueError):
                self.prompt.build_system_prompt(self.agent)
            with self.assertRaises(ValueError):
                self.sources.context_file_sources_for_agent(self.agent)
        finally:
            self.terminal_scope.reset_terminal_scope(token)
        self.environment.assert_not_called()


if __name__ == "__main__":
    unittest.main()
