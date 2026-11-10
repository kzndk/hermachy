"""Project instruction discovery, run on the workstation by hermachy-exec."""
from __future__ import annotations

import os
from pathlib import Path


def collect_context_files(cwd: str) -> list[dict[str, str]]:
    """Match the pinned Hermes context-file priority and AGENTS directory chain."""
    directory = Path(cwd)
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError("context workspace must be an existing absolute directory on Omarchy")
    directory = directory.resolve()
    ancestors = [directory, *directory.parents]
    root = next((p for p in ancestors if (p / ".git").exists()), None)
    chain = ancestors[:ancestors.index(root) + 1] if root else [directory]
    files: list[dict[str, str]] = []

    def add(path: Path, kind: str, label: str | None = None) -> bool:
        try:
            if not path.is_file():
                return False
            content = path.read_text(encoding="utf-8").strip()
            status = "read" if content else "empty"
        except (OSError, UnicodeError):
            content, status = "", "unreadable"
        files.append({"kind": kind, "label": label or os.path.relpath(path, directory),
                      "path": str(path), "content": content, "status": status})
        return bool(content)

    # The nearest .hermes.md wins, including an empty file that shadows ancestors.
    found = False
    for parent in chain:
        for name in (".hermes.md", "HERMES.md"):
            if (parent / name).is_file():
                add(parent / name, "hermes_md", name)
                found = True
                break
        if found:
            break
    for parent in reversed(chain):
        for name in ("AGENTS.override.md", "AGENTS.md", "agents.md"):
            if add(parent / name, "agents_md"):
                break
    for name in ("CLAUDE.md", "claude.md"):
        if add(directory / name, "claude_md"):
            break
    add(directory / ".cursorrules", "cursorrules")
    rules = directory / ".cursor" / "rules"
    if rules.is_dir():
        for path in sorted(rules.glob("*.mdc")):
            add(path, "cursorrules")
    return files
