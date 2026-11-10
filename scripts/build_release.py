#!/usr/bin/env python3
"""Build the versioned CLI bundle and verify or refresh its runtime checksums."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from hermachy import __version__
from scripts.build_zipapps import PLUGIN_FILES, TUI_ASSETS, build_all


def build_release(output: Path, manifest: Path, *, check: bool = False) -> Path:
    artifacts = build_all(output)
    files = {item.name: item for item in artifacts}
    files.update({f"tui/{name}": output / "tui" / name for name in TUI_ASSETS})
    files.update({f"plugin/hermachy/{name}": output / "plugin" / "hermachy" / name for name in PLUGIN_FILES})
    hashes = {name: hashlib.sha256(item.read_bytes()).hexdigest()
              for name, item in files.items()}
    if check:
        if json.loads(manifest.read_text()) != hashes:
            raise ValueError("Release checksums are stale; run python3 scripts/build_release.py")
    else:
        manifest.write_text(json.dumps(hashes, indent=2) + "\n")

    release = output / "release"
    release.mkdir(parents=True, exist_ok=True)
    archive = release / f"hermachy-{__version__}.tar.gz"
    payloads = {name: item.read_bytes() for name, item in files.items()}
    payloads["LICENSE"] = (ROOT / "LICENSE").read_bytes()
    payloads["release-sha256.json"] = manifest.read_bytes()
    for name in ("README.md", "CHANGELOG.md", "SECURITY.md", "docs/OMARCHY_REMOTE_HERMES.md"):
        payloads[name] = (ROOT / name).read_bytes()
    with archive.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as bundle:
            for name, data in sorted(payloads.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o755 if name in {item.name for item in artifacts} else 0o644
                bundle.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (release / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n")
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if committed runtime checksums differ")
    args = parser.parse_args()
    print(build_release(ROOT / "dist", ROOT / "release-sha256.json", check=args.check))


if __name__ == "__main__":
    main()
