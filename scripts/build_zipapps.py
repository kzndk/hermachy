#!/usr/bin/env python3
"""Build dependency-free, reproducible Hermachy zipapps."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Sequence
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACKAGE = ROOT / "src" / "hermachy"
ENTRYPOINTS = {
    "hermachy": ROOT / "entrypoints" / "client" / "__main__.py",
    "hermachy-server": ROOT / "entrypoints" / "server" / "__main__.py",
    "hermachy-admin": ROOT / "entrypoints" / "admin" / "__main__.py",
    "hermachy-exec": ROOT / "entrypoints" / "executor" / "__main__.py",
}
SHEBANG = b"#!/usr/bin/env python3\n"
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _archive_sources(name: str) -> Iterable[tuple[str, Path]]:
    yield "__main__.py", ENTRYPOINTS[name]
    for source in sorted(SOURCE_PACKAGE.rglob("*.py")):
        relative = source.relative_to(SOURCE_PACKAGE).as_posix()
        yield f"hermachy/{relative}", source


def _zip_info(archive_name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(archive_name, date_time=FIXED_ZIP_TIMESTAMP)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o100644 & 0xFFFF) << 16
    return info


def build_one(name: str, output_dir: Path) -> Path:
    if name not in ENTRYPOINTS:
        raise ValueError(f"unknown Hermachy artifact: {name}")
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / name
    temporary = output_dir / f".{name}.{os.getpid()}.tmp"
    with contextlib.suppress(FileNotFoundError):
        temporary.unlink()

    try:
        with temporary.open("wb") as stream:
            stream.write(SHEBANG)
            with zipfile.ZipFile(
                stream,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as archive:
                for archive_name, source in _archive_sources(name):
                    archive.writestr(
                        _zip_info(archive_name),
                        source.read_bytes(),
                        compress_type=zipfile.ZIP_DEFLATED,
                        compresslevel=9,
                    )
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o755)
        os.replace(temporary, target)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
    return target


TUI_ASSETS = ("entry.mjs", "PROVENANCE.json", "LICENSE", "activity-emojis.patch", "omarchy-theme.patch",
              "scripts/clipboard_image.py")
PLUGIN_FILES = ("plugin.yaml", "__init__.py")


def build_all(output_dir: Path) -> list[Path]:
    # Ship the renderer with the same parser revision, not just the zipapps.
    assets = {name: (ROOT / "tui" / name).read_bytes() for name in TUI_ASSETS}
    provenance = json.loads(assets["PROVENANCE.json"])
    checked = {name: data for name, data in assets.items() if name not in {"PROVENANCE.json", "LICENSE"}}
    checked["hermes_parser.py"] = (SOURCE_PACKAGE / "hermes_parser.py").read_bytes()
    if set(checked) != set(provenance["assets"]):
        raise ValueError("TUI provenance does not list exactly the shipped assets")
    for name, data in checked.items():
        if hashlib.sha256(data).hexdigest() != provenance["assets"][name]:
            raise ValueError(f"TUI provenance checksum mismatch: {name}")
    artifacts = [build_one(name, output_dir) for name in ENTRYPOINTS]
    for name, data in assets.items():
        target = output_dir / "tui" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for name in PLUGIN_FILES:
        target = output_dir / "plugin" / "hermachy" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / "plugin" / "hermachy" / name).read_bytes())
    return artifacts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Hermachy executable zipapps")
    parser.add_argument("--output-dir", default=str(ROOT / "dist"))
    args = parser.parse_args(argv)
    for artifact in build_all(Path(args.output_dir).expanduser()):
        print(artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
