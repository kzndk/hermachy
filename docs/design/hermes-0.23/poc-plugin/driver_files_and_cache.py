import json, importlib
from model_tools import handle_function_call
from gateway.session_context import scoped_session_key
def call(sid, tool, args):
    with scoped_session_key("tui:" + sid):
        return json.loads(handle_function_call(tool, args, task_id="t-" + sid, session_id=sid))
# 1. file tools in a bound session, relative path -> must resolve in the workstation cwd
r = call("bound-session", "terminal", {"command": "true"})
r = call("bound-session", "read_file", {"path": "marker.txt"})
print("read_file bound:", (r.get("content") or r.get("error") or str(r))[:80].replace("\n", " | "))
# 2. stale cache: a chat used locally first, then bound (resumed from Hermachy)
call("late-bound", "terminal", {"command": "true"})
import sys
mod = [m for n, m in sys.modules.items() if n.endswith("hermachy_poc")][0]
mod.BOUND["late-bound"] = mod.BOUND["bound-session"]
r = call("late-bound", "terminal", {"command": "pwd"})
print("late-bound after binding:", r.get("output", "").strip()[-60:])
from tools.terminal_tool import _active_environments
print("cached envs:", {k: type(v).__name__ for k, v in _active_environments.items()})
