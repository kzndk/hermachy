import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
from types import ModuleType
import unittest
from unittest import mock

from hermachy.context_files import collect_context_files
from hermachy.executor import main
from hermachy import workspace_context


class ContextDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def test_git_root_chain_and_override_order(self):
        self.write("project/.git", "gitdir: elsewhere")
        self.write("AGENTS.md", "outside repo")
        self.write("project/AGENTS.md", "root rules")
        self.write("project/src/AGENTS.override.md", "")
        self.write("project/src/AGENTS.md", "subdir rules")
        self.write("project/src/deep/AGENTS.override.md", "override rules")
        self.write("project/src/deep/AGENTS.md", "shadowed rules")
        files = collect_context_files(str(self.root / "project/src/deep"))
        self.assertEqual([f["content"] for f in files if f["content"]],
                         ["root rules", "subdir rules", "override rules"])
        self.assertEqual([f["label"] for f in files if f["content"]],
                         ["../../AGENTS.md", "../AGENTS.md", "AGENTS.override.md"])

    def test_without_git_only_workspace_instructions_are_considered(self):
        self.write("AGENTS.md", "parent must not load")
        workspace = self.root / "workspace"
        workspace.mkdir()
        self.assertEqual(collect_context_files(str(workspace)), [])
        self.write("workspace/CLAUDE.md", "local rules")
        self.assertEqual([f["content"] for f in collect_context_files(str(workspace))], ["local rules"])

    def test_nearest_hermes_file_and_all_lower_priority_candidates(self):
        self.write("project/.git", "gitdir: elsewhere")
        self.write("project/.hermes.md", "root personality")
        self.write("project/sub/HERMES.md", "nearest rules")
        self.write("project/sub/AGENTS.md", "agent rules")
        self.write("project/sub/CLAUDE.md", "claude rules")
        self.write("project/sub/.cursorrules", "cursor rules")
        self.write("project/sub/.cursor/rules/two.mdc", "two")
        self.write("project/sub/.cursor/rules/one.mdc", "one")
        files = collect_context_files(str(self.root / "project/sub"))
        self.assertEqual([f["content"] for f in files],
                         ["nearest rules", "agent rules", "claude rules", "cursor rules", "one", "two"])

    def test_missing_relative_and_non_directory_workspaces_fail(self):
        file = self.write("file", "not a directory")
        for cwd in ("relative", str(self.root / "missing"), str(file)):
            with self.subTest(cwd=cwd), self.assertRaises(ValueError):
                collect_context_files(cwd)

    def test_symlinks_are_resolved_on_the_workstation(self):
        self.write("actual/.git", "gitdir: elsewhere")
        self.write("actual/AGENTS.md", "real workspace")
        (self.root / "linked").symlink_to(self.root / "actual", target_is_directory=True)
        files = collect_context_files(str(self.root / "linked"))
        self.assertEqual(files[0]["path"], str(self.root / "actual/AGENTS.md"))

    def test_executor_outputs_only_context_json_without_desktop_or_hermes(self):
        self.write("AGENTS.md", "workstation instructions")
        output = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
                "hermachy.executor.desktop_environment") as desktop, contextlib.redirect_stdout(output):
            self.assertEqual(main(["--context-files", str(self.root)]), 0)
        desktop.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())[0]["content"], "workstation instructions")


class WorkspacePromptTests(unittest.TestCase):
    def setUp(self):
        self.runtime = ModuleType("agent.runtime_cwd")
        self.runtime.scoped_session_cwd = lambda: ""
        self.runtime.scope_terminal_cwd = lambda: "/omarchy/project"
        self.pb = ModuleType("agent.prompt_builder")
        self.pb._strip_yaml_frontmatter = lambda text: text
        self.pb._context_section = mock.Mock(side_effect=lambda text, label, *_: f"## {label}\n\n{text}")
        self.pb._truncate_content = mock.Mock(side_effect=lambda text, *_args, **_kwargs: text)
        self.pb.load_soul_md = mock.Mock(return_value="host SOUL instructions")
        agent = ModuleType("agent")
        agent.prompt_builder = self.pb
        self.lifecycle = ModuleType("tools.terminal_tool_lifecycle")
        self.backend = mock.Mock(hermachy_safe_executor=True)
        self.lifecycle.ensure_task_env = mock.Mock(return_value=self.backend)
        self.modules = {"agent": agent, "agent.runtime_cwd": self.runtime,
                        "agent.prompt_builder": self.pb, "tools.terminal_tool_lifecycle": self.lifecycle}
        self.addCleanup(mock.patch.stopall)
        mock.patch.dict(sys.modules, self.modules).start()

    def response(self, files):
        self.backend.execute.return_value = {"returncode": 0, "output": json.dumps(files)}

    def item(self, content, kind="agents_md", name="AGENTS.md"):
        return dict(kind=kind, label=name, path="/omarchy/project/" + name,
                    content=content, status="read" if content else "empty")

    def test_workspace_identity_never_uses_the_host_working_directory(self):
        self.assertEqual(str(workspace_context.workspace_cwd()), "/omarchy/project")
        self.runtime.scoped_session_cwd = lambda: "/different/workspace"
        self.assertEqual(str(workspace_context.workspace_cwd()), "/different/workspace")
        self.assertEqual(str(workspace_context.workspace_cwd("/explicit")), "/explicit")
        self.runtime.scoped_session_cwd = lambda: ""
        self.runtime.scope_terminal_cwd = lambda: ""
        with self.assertRaises(ValueError):
            workspace_context.workspace_cwd()

    def test_launch_artifact_can_skip_session_override(self):
        self.runtime.scoped_session_cwd = mock.Mock(return_value="/host/launch-artifact")
        self.assertEqual(workspace_context.workspace_cwd(include_session_override=False),
                         Path("/omarchy/project"))
        self.runtime.scoped_session_cwd.assert_not_called()
        self.assertEqual(workspace_context.workspace_cwd(include_session_override=True),
                         Path("/host/launch-artifact"))

    def test_explicit_workspace_wins_when_session_override_is_disabled(self):
        self.runtime.scoped_session_cwd = mock.Mock()
        self.runtime.scope_terminal_cwd = mock.Mock()
        self.assertEqual(workspace_context.workspace_cwd("/explicit", include_session_override=False),
                         Path("/explicit"))
        self.runtime.scoped_session_cwd.assert_not_called()
        self.runtime.scope_terminal_cwd.assert_not_called()

    def test_skipped_session_cannot_supply_a_missing_configured_workspace(self):
        self.runtime.scoped_session_cwd = lambda: "/host/launch-artifact"
        self.runtime.scope_terminal_cwd = lambda: ""
        with self.assertRaises(ValueError):
            workspace_context.workspace_cwd(include_session_override=False)

    def test_empty_workspace_does_not_load_host_project_even_when_fallback_allowed(self):
        self.response([])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "AGENTS.md").write_text("host source tree instructions must not load")
            result = workspace_context.build_context_files_prompt(path, skip_soul=True,
                                                                  allow_install_tree_fallback=True)
        self.assertEqual(result, "")
        self.pb._context_section.assert_not_called()

    def test_project_priority_deduplication_and_native_scanning_and_cap_hooks(self):
        self.response([self.item("root"), self.item("root"), self.item("child", name="src/AGENTS.md"),
                       self.item("shadowed", "claude_md", "CLAUDE.md")])
        result = workspace_context.build_context_files_prompt(skip_soul=True, context_length=272000)
        self.assertEqual(result.count("root"), 1)
        self.assertIn("child", result)
        self.assertNotIn("shadowed", result)
        self.assertEqual(self.pb._context_section.call_count, 2)
        self.assertEqual(self.pb._context_section.call_args.args[-1], 272000)
        self.pb._truncate_content.assert_called_once()
        self.assertEqual(self.pb._truncate_content.call_args.kwargs["read_path"],
                         "/omarchy/project/src/AGENTS.md")
        self.pb.load_soul_md.assert_not_called()
        self.response([self.item("highest", "hermes_md", ".hermes.md"), self.item("lower")])
        result = workspace_context.build_context_files_prompt(skip_soul=True)
        self.assertIn("highest", result)
        self.assertNotIn("lower", result)

    def test_host_soul_identity_is_preserved(self):
        self.response([])
        result = workspace_context.build_context_files_prompt(home_override=Path("/host/profile"))
        self.assertIn("host SOUL instructions", result)
        self.pb.load_soul_md.assert_called_once_with(None, home_override=Path("/host/profile"))

    def test_connection_and_response_failures_never_load_host_context(self):
        for response in ({"returncode": 255, "output": "disconnected"},
                         {"returncode": 0, "output": "invalid JSON"},
                         {"returncode": 0, "output": "{}"},
                         {"returncode": 0, "output": '[{"content":"incomplete"}]'}):
            self.backend.execute.return_value = response
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                workspace_context.build_context_files_prompt()
        self.pb._context_section.assert_not_called()

    def test_context_reads_require_protected_backend_and_quote_paths(self):
        self.response([])
        path = "/omarchy/project ' ; $(touch wrong-host)"
        workspace_context.read_context_files(path)
        self.assertEqual(shlex.split(self.backend.execute.call_args.args[0]),
                         ["hermachy-exec", "--context-files", path])
        self.backend.hermachy_safe_executor = False
        with self.assertRaises(RuntimeError):
            workspace_context.read_context_files()

    def test_install_updates_parent_and_child_imports(self):
        self.runtime.resolve_context_cwd = lambda: Path("/host/source")
        self.runtime.resolve_agent_cwd = self.runtime.resolve_context_cwd
        self.pb.build_context_files_prompt = lambda **kwargs: "host source instructions"
        sources = ModuleType("agent.context_file_sources")
        sources.list_context_file_sources = lambda **kwargs: []
        parent = ModuleType("agent.system_prompt")
        parent.resolve_context_cwd = self.runtime.resolve_context_cwd
        child = ModuleType("tools.delegate_tool_progress")
        child.build_context_files_prompt = self.pb.build_context_files_prompt
        with mock.patch.dict(sys.modules, {"agent.context_file_sources": sources,
                                          "agent.system_prompt": parent, "tools.delegate_tool_progress": child}):
            workspace_context.install()
            self.response([self.item("workstation instructions")])
            self.assertEqual(str(parent.resolve_context_cwd(include_session_override=False)), "/omarchy/project")
            self.assertIn("workstation instructions", child.build_context_files_prompt(skip_soul=True))
            self.assertIs(sources.list_context_file_sources, workspace_context.list_context_file_sources)
            workspace_context.install()
            self.assertEqual(str(parent.resolve_context_cwd()), "/omarchy/project")
