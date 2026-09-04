#!/usr/bin/env python3
"""Build dependency-free, reproducible Hermes Link zipapps."""

from __future__ import annotations

import argparse
import contextlib
import os
from pathlib import Path
from typing import Iterable, Sequence
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACKAGE = ROOT / "src" / "hermes_link"
ENTRYPOINTS = {
    "hlink": ROOT / "entrypoints" / "client" / "__main__.py",
    "hlink-server": ROOT / "entrypoints" / "server" / "__main__.py",
    "hlink-admin": ROOT / "entrypoints" / "admin" / "__main__.py",
}
SHEBANG = b"#!/usr/bin/env python3\n"
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _archive_sources(name: str) -> Iterable[tuple[str, Path]]:
    yield "__main__.py", ENTRYPOINTS[name]
    for source in sorted(SOURCE_PACKAGE.rglob("*.py")):
        relative = source.relative_to(SOURCE_PACKAGE).as_posix()
        yield f"hermes_link/{relative}", source


def _zip_info(archive_name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(archive_name, date_time=FIXED_ZIP_TIMESTAMP)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o100644 & 0xFFFF) << 16
    return info


def build_one(name: str, output_dir: Path) -> Path:
    if name not in ENTRYPOINTS:
        raise ValueError(f"unknown Hermes Link artifact: {name}")
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
