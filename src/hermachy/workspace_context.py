"""Read project instructions over the protected workstation connection.

Hermes profile instructions stay on the host. Project instructions always come
from Omarchy, even when the same path happens to exist on the Hermes host.
"""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import sys


def workspace_cwd(cwd=None, *, include_session_override: bool = True) -> Path:
    from agent.runtime_cwd import scoped_session_cwd, scope_terminal_cwd
    if cwd is not None:
        raw = str(cwd)
    else:
        # Hermes 0.22 skips session paths known to be host launch artifacts.
        # Keep its selection policy without validating Linux paths on the host.
        raw = scoped_session_cwd() if include_session_override else ""
        raw = raw or scope_terminal_cwd()
    if not raw or not raw.startswith("/") or any(ord(c) < 32 or ord(c) == 127 for c in raw):
        raise ValueError("Hermachy project context requires an absolute Omarchy workspace")
    # Never resolve or stat this path on the host (/tmp is a different filesystem too).
    return Path(raw)


def read_context_files(cwd=None) -> list[dict[str, str]]:
    from tools.terminal_tool_lifecycle import ensure_task_env
    path = workspace_cwd(cwd)
    environment = ensure_task_env()
    if environment is None or not getattr(environment, "hermachy_safe_executor", False):
        raise RuntimeError("project context requires the protected Omarchy environment")
    result = environment.execute("hermachy-exec --context-files " + shlex.quote(str(path)), timeout=15)
    if result.get("returncode") != 0:
        raise RuntimeError("Could not read project instructions from Omarchy; check the connection and upgrade hermachy-exec")
    try:
        files = json.loads(result.get("output", ""))
        if not isinstance(files, list) or any(
            not isinstance(item, dict)
            or not all(isinstance(item.get(key), str) for key in ("kind", "label", "path", "content", "status"))
            or item["kind"] not in {"hermes_md", "agents_md", "claude_md", "cursorrules"}
            or not item["path"].startswith("/")
            for item in files
        ):
            raise ValueError("invalid context file list")
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Invalid project context response from Omarchy") from exc
    return files


def build_context_files_prompt(cwd=None, skip_soul=False, context_length=None,
                               allow_install_tree_fallback=False, home_override=None) -> str:
    from agent import prompt_builder as pb
    files = read_context_files(cwd)
    winner = next((item["kind"] for item in files if item["content"]), None)
    sections, seen, read_path = [], set(), ""
    for item in files:
        content = item["content"]
        if item["kind"] != winner or not content or (winner == "agents_md" and content in seen):
            continue
        seen.add(content)
        if winner == "hermes_md":
            content = pb._strip_yaml_frontmatter(content)
        read_path = item["path"]
        sections.append(pb._context_section(content, item["label"], item["label"],
                                            Path(item["path"]), context_length))
    project = "\n\n".join(sections)
    if len(sections) > 1:
        project = pb._truncate_content(project, "project context (directory chain)",
                                       context_length=context_length, read_path=read_path)
    sections = [project] if project else []
    if not skip_soul:
        soul = pb.load_soul_md(context_length, home_override=home_override)
        if soul:
            sections.append(soul)
    if not sections:
        return ""
    return ("# Project Context\n\nThe following project context files have been loaded and should be followed:\n\n"
            + "\n".join(sections))


def list_context_file_sources(cwd=None, context_length=None, allow_install_tree_fallback=False,
                              home_override=None, skip_soul=False):
    from agent import context_file_sources as sources, prompt_builder as pb
    max_chars = pb._get_context_file_max_chars(context_length)
    result, winner = [], None
    for item in read_context_files(cwd):
        content = item["content"]
        if not content:
            status = item["status"]
        elif winner in (None, item["kind"]):
            winner = item["kind"]
            status = sources._loaded_status(content, len(f"## {item['label']}\n\n{content}"), max_chars)
        else:
            status = "shadowed"
        result.append(sources._entry(item["label"], Path(item["path"]), content, status))
    if not skip_soul:
        home = Path(home_override) if home_override is not None else pb.get_hermes_home()
        path = home / "SOUL.md"
        if path.exists():
            content = pb._read_context_file(path)
            status = (sources._loaded_status(content, len(content), max_chars, user_authored=True)
                      if content else sources._empty_status(path))
            result.append(sources._entry("SOUL.md", path, content, status))
    return result


def install() -> None:
    from agent import context_file_sources, prompt_builder, runtime_cwd
    # Replace existing imports as well as the definitions: the terminal backend
    # can import prompt assembly before this Hermachy extension is installed.
    replacements = {
        runtime_cwd.resolve_context_cwd: workspace_cwd,
        runtime_cwd.resolve_agent_cwd: workspace_cwd,
        prompt_builder.build_context_files_prompt: build_context_files_prompt,
        context_file_sources.list_context_file_sources: list_context_file_sources,
    }
    for name, module in tuple(sys.modules.items()):
        if name.startswith(("agent.", "tools.", "hermes_cli.")):
            for key, value in tuple(vars(module).items()):
                if callable(value):
                    for original, replacement in replacements.items():
                        if value is original:
                            setattr(module, key, replacement)
