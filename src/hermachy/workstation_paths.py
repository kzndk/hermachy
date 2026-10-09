"""Keep native file-tool paths on the workstation filesystem."""
from __future__ import annotations

import json
import shlex
import sys


def expand_tilde(path: str) -> str:
    if not path or not path.startswith("~"):
        return path
    from tools.terminal_tool_lifecycle import ensure_task_env
    environment = ensure_task_env()
    if environment is None or not getattr(environment, "hermachy_safe_executor", False):
        raise RuntimeError("workstation path expansion requires the protected SSH environment")
    # Resolve ~ and ~user on Omarchy, never against the Hermes host's HOME or
    # user database. The path is an argument, not a shell expression.
    script = "import json,os,sys;print(json.dumps(os.path.expanduser(sys.argv[1])))"
    result = environment.execute("python3 -c " + shlex.quote(script) + " " + shlex.quote(path), timeout=10)
    try:
        expanded = json.loads(result.get("output", ""))
    except (ValueError, TypeError) as exc:
        raise RuntimeError("could not expand the workstation home directory") from exc
    if result.get("returncode") != 0 or not isinstance(expanded, str) or not expanded.startswith("/"):
        raise RuntimeError("could not expand the workstation home directory")
    return expanded


def install() -> None:
    from tools import file_tools_paths
    original = file_tools_paths._expand_tilde
    # The pinned upstream path resolver treats SSH as a host filesystem and
    # follows the Mac host's symlinks (/tmp -> /private/tmp). Its pure-POSIX
    # branch already provides exactly the non-host-dereferencing path behavior
    # needed here. This changes file path handling only, not backend routing.
    file_tools_paths._uses_container_paths = lambda task_id="default": True
    file_tools_paths._expand_tilde = expand_tilde
    # Some guards may already hold an imported reference. Keep them consistent
    # with the resolver so ~ paths cannot pass guards on one machine and be
    # written on the other.
    for name, module in tuple(sys.modules.items()):
        if name.startswith("tools.file_tools") and getattr(module, "_expand_tilde", None) is original:
            module._expand_tilde = expand_tilde
