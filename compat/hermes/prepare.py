#!/usr/bin/env python3
"""Fetch and verify Hermachy's pinned Hermes source (Python 3.9+, Git)."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

MANIFEST = Path(__file__).with_name("manifest.json")


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(directory), *args],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Git source verification failed")
    return result.stdout.strip()


def read_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text())
    for key in ("revision", "patched_tree"):
        if not re.fullmatch(r"[0-9a-f]{40}", manifest[key]):
            raise ValueError(f"Invalid {key} in compatibility manifest")
    if "release" in manifest and not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", manifest["release"]):
        raise ValueError("Invalid release tag in compatibility manifest")
    patch = path.with_name("safe-executor-v2.patch")
    if hashlib.sha256(patch.read_bytes()).hexdigest() != manifest["patch_sha256"]:
        raise ValueError("Compatibility patch checksum mismatch")
    return manifest


def verify_source(destination: Path, manifest: dict) -> None:
    if Path(git(destination, "rev-parse", "--show-toplevel")).resolve() != destination.resolve():
        raise ValueError("Destination must be the Hermes checkout root")
    if git(destination, "rev-parse", "HEAD") != manifest["revision"]:
        raise ValueError("Hermes revision differs from the compatibility manifest")
    if "release" in manifest and git(destination, "rev-parse", manifest["release"] + "^{}") != manifest["revision"]:
        raise ValueError("Hermes release tag differs from the pinned revision")
    if git(destination, "write-tree") != manifest["patched_tree"]:
        raise ValueError("Hermes patch differs from the compatibility manifest")
    # The patch stays staged. Check both the index above and working files here.
    if git(destination, "diff", "--name-only", "--no-ext-diff"):
        raise ValueError("Hermes working files differ from the prepared source")
    if git(destination, "ls-files", "--others", "--exclude-standard"):
        raise ValueError("Unexpected untracked files in Hermes checkout")


def prepare(destination: Path, manifest_path: Path = MANIFEST) -> None:
    manifest = read_manifest(manifest_path)
    if destination.exists() or destination.is_symlink():
        raise ValueError("Destination already exists; choose a new directory")
    destination.mkdir(parents=True)
    git(destination, "init", "--quiet")
    git(destination, "remote", "add", "origin", manifest["repository"])
    print(f"Fetching Hermes {manifest['revision']}…", flush=True)
    refs = [manifest["revision"]]
    if "release" in manifest:
        refs.append(f"refs/tags/{manifest['release']}:refs/tags/{manifest['release']}")
    git(destination, "-c", "credential.helper=", "fetch", "--depth=1", "origin", *refs)
    git(destination, "checkout", "--detach", manifest["revision"])
    patch = str(manifest_path.with_name("safe-executor-v2.patch").resolve())
    git(destination, "apply", "--index", "--check", patch)
    git(destination, "apply", "--index", patch)
    verify_source(destination, manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="New Hermes source directory")
    parser.add_argument("--check", action="store_true", help="Verify an existing prepared source tree without fetching")
    args = parser.parse_args()
    destination = args.destination.expanduser().absolute()
    try:
        if args.check:
            verify_source(destination, read_manifest(MANIFEST))
        else:
            prepare(destination)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Hermes preparation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Verified Hermes source: {destination}")
    if not args.check:
        print("Next: install dependencies and configure Hermes as described in compat/hermes/README.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
