import json
from model_tools import handle_function_call
from gateway.session_context import scoped_session_key
def turn(sid, cmd="pwd"):
    with scoped_session_key("tui:" + sid):            # what the gateway binds per turn
        r = json.loads(handle_function_call("terminal", {"command": cmd, "timeout": 20}, task_id="t-" + sid, session_id=sid))
    print(f"{sid:14s} exit={r.get('exit_code')} output={r.get('output','').strip()!r}")
turn("bound-session"); turn("other-session"); turn("bound-session"); turn("other-session")
from tools.terminal_tool import _active_environments
print("cached envs:", {k: type(v).__name__ for k, v in _active_environments.items()})
