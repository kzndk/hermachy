#!/usr/bin/env python3
"""Build dependency-free Hermes Link zipapp executables."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import tempfile
from typing import Sequence
import zipapp

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACKAGE = ROOT / "src" / "hermes_link"
ENTRYPOINTS = {
    "hlink": ROOT / "entrypoints" / "client" / "__main__.py",
    "hlink-server": ROOT / "entrypoints" / "server" / "__main__.py",
    "hlink-admin": ROOT / "entrypoints" / "admin" / "__main__.py",
}
ZIP_MIN_EPOCH = 315532800  # 1980-01-01, the oldest timestamp supported by ZIP.


def _source_epoch() -> int:
    raw = os.environ.get("SOURCE_DATE_EPOCH", str(ZIP_MIN_EPOCH))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("SOURCE_DATE_EPOCH must be an integer") from exc
    return max(value, ZIP_MIN_EPOCH)


def _normalize_tree_timestamps(root: Path, epoch: int) -> None:
    paths = sorted(root.rglob("*"), key=lambda item: (len(item.parts), str(item)), reverse=True)
    for path in paths:
        os.utime(path, (epoch, epoch), follow_symlinks=False)
    os.utime(root, (epoch, epoch))


def build_one(name: str, output_dir: Path) -> Path:
    if name not in ENTRYPOINTS:
        raise ValueError(f"unknown Hermes Link artifact: {name}")
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / name
    temporary_target = output_dir / f".{name}.{os.getpid()}.tmp"
    if temporary_target.exists():
        temporary_target.unlink()

    with tempfile.TemporaryDirectory(prefix=f"hlink-{name}-") as directory:
        staging = Path(directory)
        shutil.copytree(
            SOURCE_PACKAGE,
            staging / "hermes_link",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        shutil.copy2(ENTRYPOINTS[name], staging / "__main__.py")
        _normalize_tree_timestamps(staging, _source_epoch())
        zipapp.create_archive(
            staging,
            target=temporary_target,
            interpreter="/usr/bin/env python3",
            compressed=True,
        )
    temporary_target.chmod(0o755)
    os.replace(temporary_target, target)
    return target


def build_all(output_dir: Path) -> list[Path]:
    return [build_one(name, output_dir) for name in ENTRYPOINTS]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Hermes Link executable zipapps")
    parser.add_argument("--output-dir", default=str(ROOT / "dist"))
    args = parser.parse_args(argv)
    for artifact in build_all(Path(args.output_dir).expanduser()):
        print(artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
