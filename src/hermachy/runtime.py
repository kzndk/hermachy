"""Hermachy-only Hermes extension; the installed Hermes source is untouched."""
from __future__ import annotations

import json
import os
import copy
from pathlib import Path
import runpy

def execute(args, **context) -> str:
    from tools import terminal_tool as terminal

    target = args.get("target")
    command = args.get("command")
    timeout = args.get("timeout", 180)
    workdir = args.get("workdir")
    error = None
    if not isinstance(target, str) or target not in {"omarchy", "hermes"}:
        error = "target must be omarchy or hermes"
    elif not isinstance(command, str) or not command.strip() or "\x00" in command:
        error = "command must be a non-empty string without NUL bytes"
    elif type(timeout) is not int or not 1 <= timeout <= 300:
        error = "timeout must be an integer between 1 and 300"
    elif workdir is not None and (
        not isinstance(workdir, str) or not workdir.startswith("/")
        or any(ord(char) < 32 for char in workdir)
    ):
        error = "workdir must be an absolute path on the selected machine"
    elif set(args) - {"target", "command", "timeout", "workdir"}:
        error = "unsupported execution arguments"
    if error:
        return json.dumps({"target": target, "status": "error", "error": error})
    if target == "omarchy":
        result = json.loads(terminal.terminal_tool(
            command=command, timeout=timeout, workdir=workdir,
            task_id=context.get("task_id"), session_id=context.get("session_id"),
        ))
    else:
        # A separate environment object provides intentional host execution;
        # never alter TERMINAL_ENV or relax the protected Omarchy route. The
        # same Hermes approval callbacks run in the calling session/thread.
        decision = terminal._check_all_guards(command, "local")
        if not decision.get("approved"):
            result = dict(decision, output="", exit_code=-1,
                          error=decision.get("message") or "Command requires approval",
                          status=decision.get("status", "blocked"))
        else:
            from tools.environments.local import LocalEnvironment
            directory = workdir or str(Path.home())
            if not Path(directory).is_dir():
                return json.dumps({"target": target, "status": "error",
                                   "error": "working directory does not exist on Hermes"})
            environment = LocalEnvironment(cwd=directory, timeout=timeout)
            try:
                result = environment.execute(command)
                result["exit_code"] = result.pop("returncode", -1)
                result["workdir"] = directory
            finally:
                environment.cleanup()
    result["target"] = target
    return json.dumps(result, ensure_ascii=False)


def route_terminal(args, **context) -> str:
    """Extend Hermes's always-visible terminal, preserving native local features."""
    from tools import terminal_tool as terminal
    target = args.get("target", "omarchy")
    if target == "omarchy":
        result = json.loads(terminal._handle_terminal(
            {key: value for key, value in args.items() if key != "target"}, **context))
        result["target"] = target
        return json.dumps(result, ensure_ascii=False)
    if any(args.get(key) for key in ("background", "pty", "notify", "notify_on_complete", "watch_patterns")):
        return json.dumps({"target": target, "status": "error",
                           "error": "Hermes target supports foreground commands only, up to 300 seconds"})
    return execute({key: value for key, value in args.items()
                    if key not in {"background", "pty", "notify", "notify_on_complete", "watch_patterns"}},
                   **context)


def install():
    if os.environ.get("HERMACHY_REQUIRE_SAFE_EXECUTOR") != "1":
        raise RuntimeError("Hermachy execution extension requires its protected launcher")
    from hermes_cli.safe_executor import validate_hermachy_safe_executor_env
    validate_hermachy_safe_executor_env()
    # Keep the command/file/code SSH leg alive too. This process-scoped
    # subclass retains the tested Hermes identity and no-sync implementation.
    from tools.environments import ssh
    if not getattr(ssh.SSHEnvironment, "_hermachy_keepalive", False):
        class KeepaliveSSHEnvironment(ssh.SSHEnvironment):
            _hermachy_keepalive = True

            def _build_ssh_command(self, extra_args=None):
                argv = super()._build_ssh_command(extra_args)
                return [argv[0], "-o", "ServerAliveInterval=15",
                        "-o", "ServerAliveCountMax=3", *argv[1:]]

        ssh.SSHEnvironment = KeepaliveSSHEnvironment
    # Extend the existing core terminal schema so routing is visible on every
    # turn, including when Hermes defers extension tools behind tool_search.
    from tools import terminal_tool as terminal
    from tools.registry import registry
    schema = copy.deepcopy(terminal.TERMINAL_SCHEMA)
    schema["description"] = (
        "Hermachy execution: target='omarchy' (default) runs on the user's Linux "
        "workstation; target='hermes' runs on the remote Hermes host. Choose from "
        "the task. Never retry a failed command on the other machine. Use Omarchy "
        "for desktop commands and local projects; use Hermes for remote host "
        "files, services and installed CLI tools. Native read_file/write_file/"
        "patch/search_files/execute_code remain on Omarchy; for remote file and "
        "code operations use shell/Python commands with target='hermes'. Workdir "
        "belongs to the selected machine. Hermes defaults to the remote user's "
        "home and supports foreground commands up to 300 seconds. Omarchy "
        "defaults to the session workspace and retains background/process tools. "
        "Discover desktop commands with 'omarchy commands'.\n\n"
        + schema["description"]
    )
    schema["parameters"]["properties"]["target"] = {
        "type": "string", "enum": ["omarchy", "hermes"], "default": "omarchy",
        "description": "Machine to execute on: local Omarchy workstation or remote Hermes host",
    }
    registry.register(name="terminal", toolset="terminal", schema=schema,
                      handler=route_terminal, check_fn=terminal.check_terminal_requirements, emoji="💻",
                      max_result_size_chars=100_000)


if __name__ == "__main__":
    install()
    runpy.run_module("tui_gateway.entry", run_name="__main__")
