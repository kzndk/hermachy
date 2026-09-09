"""Run on the Hermes host with its venv: check candidate tools without a model call."""
import argparse
import json
import os
import platform
from pathlib import Path
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--archive", default=str(Path("~/.local/bin/hermachy-server").expanduser()))
parser.add_argument("--cwd", required=True, help="Existing absolute Omarchy workspace")
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
