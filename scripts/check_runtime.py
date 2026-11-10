"""Run on the Hermes host with its venv: check candidate tools without a model call."""
import argparse
import json
import os
import platform
from pathlib import Path
import shlex
import sys
import tempfile
from types import SimpleNamespace
import uuid
from unittest import mock

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--archive", default=str(Path("~/.local/bin/hermachy-server").expanduser()))
parser.add_argument("--cwd", required=True, help="Existing absolute Omarchy workspace")
parser.add_argument("--access", action="store_true", help="Check desktop, file/code tools and uncertain-write handling using temporary files")
parser.add_argument("--context-cwd", help="Existing Omarchy project with instruction files for a read-only prompt check")
args = parser.parse_args()
sys.path.insert(0, args.archive)
from hermachy.server import load_server_config, build_hermes_env, _server_config_path
from hermachy.protocol import LaunchRequest

config = load_server_config(_server_config_path())
os.environ.update(build_hermes_env(config, LaunchRequest(args.cwd, "gateway")))
sys.path.insert(0, str(config.hermes_source_root))
from hermachy.runtime import install
install()
from model_tools import get_tool_definitions, handle_function_call

definitions = get_tool_definitions(enabled_toolsets=["terminal"], skip_tool_search_assembly=True)
schema = next(tool["function"] for tool in definitions if tool["function"]["name"] == "terminal")
assert schema["parameters"]["properties"]["target"]["enum"] == ["omarchy", "hermes"]
print("Model can discover the execution target tool", flush=True)
for target, expected in (("hermes", platform.system()), ("omarchy", "Linux")):
    result = json.loads(handle_function_call("terminal", {
        "target": target, "command": "uname -s; pwd", "timeout": 30,
    }, task_id="hermachy-verify-" + target))
    assert result.get("exit_code") == 0, result
    assert result["target"] == target, result
    assert expected in result.get("output", ""), result
    print(json.dumps(result), flush=True)
print("Both execution targets passed", flush=True)
from tools.environments.ssh import SSHEnvironment
assert getattr(SSHEnvironment, "_hermachy_keepalive", False)
print("Execution SSH keepalives enabled", flush=True)

if args.access:
    from tools.file_tools import read_file_tool, write_file_tool, patch_tool
    from tools.code_execution_tool import execute_code
    from tools.terminal_tool_lifecycle import get_active_env, cleanup_all_environments

    task = "hermachy-access-" + uuid.uuid4().hex
    directory = "/tmp/" + task

    def terminal(command):
        result = json.loads(handle_function_call("terminal", {
            "target": "omarchy", "command": command, "timeout": 30,
        }, task_id=task))
        return result

    created = False
    try:
        result = terminal("hermachy-exec --doctor")
        assert result.get("exit_code") == 0, result
        access = json.loads(result["output"])
        assert all(access.get(key) for key in ("ssh_entrypoint", "desktop", "wayland", "user_bus")), access
        print("Real Hermes terminal: desktop, Wayland and user bus passed", flush=True)
        from tools.file_tools_paths import _expand_tilde
        result = terminal('printf %s "$HOME"')
        assert _expand_tilde("~") == result["output"].strip(), result
        result = terminal("umask 077; mkdir " + shlex.quote(directory))
        assert result.get("exit_code") == 0, result
        created = True
        path = directory + "/file.txt"
        result = json.loads(write_file_tool(path, "HER M ACHY\n", task_id=task))
        assert not result.get("error"), result
        result = json.loads(read_file_tool(path, task_id=task))
        assert "HER M ACHY" in json.dumps(result), result
        result = json.loads(patch_tool(path=path, old_string="HER M ACHY", new_string="HERMACHY_ACCESS_OK", task_id=task))
        assert not result.get("error"), result
        result = json.loads(execute_code(
            f"import platform,pathlib;assert platform.system()=='Linux';print(pathlib.Path({path!r}).read_text())",
            task_id=task,
        ))
        assert "HERMACHY_ACCESS_OK" in json.dumps(result), result
        print("Native write/read/patch/code tools reached Omarchy", flush=True)

        # Exercise Hermes's real 0.22 callers, not only Hermachy's prompt hook.
        from agent.runtime_cwd import set_session_cwd, reset_session_cwd
        from agent.system_prompt import build_system_prompt
        from agent.context_file_sources import context_file_sources_for_agent
        context_cwd = args.context_cwd or args.cwd
        with tempfile.TemporaryDirectory(prefix="hermachy-prompt-profile-") as profile:
            agent = SimpleNamespace(
                load_soul_identity=False, skip_context_files=False, valid_tool_names=set(),
                _task_completion_guidance=False, _tool_use_enforcement=False,
                _environment_probe=False, _kanban_worker_guidance="", _memory_store=None,
                _memory_manager=None, model="", provider="", platform="tui",
                pass_session_id=False, session_id="",
                _session_db=SimpleNamespace(db_path=profile + "/state.db"),
                _emit_diagnostic_status=lambda *_args, **_kwargs: None,
            )
            for launch_artifact in (False, True):
                agent._context_cwd_is_launch_artifact = launch_artifact
                token = set_session_cwd("/host/launch-artifact" if launch_artifact else context_cwd)
                try:
                    with mock.patch.dict(os.environ, {"TERMINAL_CWD": context_cwd}):
                        prompt = build_system_prompt(agent)
                        sources = context_file_sources_for_agent(agent)
                        loaded = [item for item in sources if item["loaded"]]
                        if args.context_cwd:
                            assert loaded, "No project instructions loaded from the supplied context workspace"
                        if loaded:
                            assert "# Project Context" in prompt and any(item["label"] in prompt for item in loaded)
                finally:
                    reset_session_cwd(token)
        print("Hermes prompt assembly and context listing loaded Omarchy instructions with both cwd policies", flush=True)

        environment = get_active_env(task)
        once_path = directory + "/once.txt"
        command = "python3 -c " + shlex.quote(f"with open({once_path!r},'a') as f: f.write('once\\n')")
        base = type(environment).__mro__[1]
        original_execute = base.execute

        def lose_result(self, actual_command, *positional, **keywords):
            result = original_execute(self, actual_command, *positional, **keywords)
            if actual_command == command:
                raise OSError("injected failure after the command wrote its file")
            return result

        with mock.patch.object(base, "execute", lose_result):
            result = terminal(command)
        assert result.get("exit_code") == 255 and "uncertain" in result.get("output", ""), result
        result = terminal("cat " + shlex.quote(once_path))
        assert result.get("exit_code") == 0 and result["output"].strip() == "once", result
        print("Injected failure after write: executed once, no automatic retry", flush=True)
    finally:
        if created:
            result = terminal("python3 -c " + shlex.quote(f"import shutil;shutil.rmtree({directory!r})"))
            assert result.get("exit_code") == 0, result
        cleanup_all_environments()
