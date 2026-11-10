"""PoC only. A bound session's tool calls run under a hermachy_ssh terminal scope."""
import os
from agent.terminal_env_provider import TerminalEnvironmentProvider

BOUND = {"bound-session": os.environ["POC_WORKSTATION_CWD"]}   # bridge-written binding in the real design


class _Provider(TerminalEnvironmentProvider):
    name = "hermachy_ssh"
    is_remote = True
    is_container = False          # keep dangerous-command approvals
    def is_available(self):
        return True
    def create_environment(self, cwd=None, timeout=None, task_id=None, **kwargs):
        from tools.environments.local import LocalEnvironment
        class FakeWorkstation(LocalEnvironment):   # stands in for SSHEnvironment(sync_files=False)
            def execute(self, command, *a, **k):
                result = super().execute(command, *a, **k)
                result["output"] = "[routed:hermachy_ssh] " + result.get("output", "")
                return result
        return FakeWorkstation(cwd=cwd, timeout=timeout or 60)


def _route(tool_name, args, next_call, session_id="", **_):
    cwd = BOUND.get(session_id)
    if cwd is None:
        return next_call(args)
    try:
        from tools.terminal_scope import get_terminal_scope, set_terminal_scope, reset_terminal_scope, default_terminal_scope
        scope = dict(get_terminal_scope() or default_terminal_scope())
        scope.update({"TERMINAL_ENV": "hermachy_ssh", "TERMINAL_CWD": cwd})
        token = set_terminal_scope(scope)
    except Exception as exc:  # never let the chain skip us (fail closed)
        return '{"status":"error","error":"hermachy routing unavailable: %s"}' % type(exc).__name__
    try:
        return next_call(args)
    finally:
        reset_terminal_scope(token)


def register(ctx):
    ctx.register_terminal_environment_provider(_Provider())
    ctx.register_middleware("tool_execution", _route)
