import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

from scripts.build_zipapps import build_all


class ArtifactTests(unittest.TestCase):
    def test_builds_executable_smoke_tested_zipapps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            artifacts = build_all(output)
            self.assertEqual(
                [path.name for path in artifacts],
                ["hlink", "hlink-server", "hlink-admin"],
            )
            for path in artifacts:
                with self.subTest(path=path):
                    self.assertTrue(path.read_bytes().startswith(b"#!/usr/bin/env python3\n"))
                    self.assertTrue(stat.S_IMODE(path.stat().st_mode) & stat.S_IXUSR)

            cases = [
                (output / "hlink", ["--version"], "Hermes Link 0.1.3"),
                (output / "hlink-server", ["--version"], "Hermes Link server 0.1.3"),
                (output / "hlink-admin", ["--help"], "Install and revoke Hermes Link"),
            ]
            for executable, args, expected in cases:
                with self.subTest(executable=executable):
                    result = subprocess.run(
                        [str(executable), *args], capture_output=True, text=True, check=False
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(expected, result.stdout)

    def test_build_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_artifacts = build_all(Path(first))
            second_artifacts = build_all(Path(second))
            first_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in first_artifacts]
            second_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in second_artifacts]
            self.assertEqual(first_hashes, second_hashes)

    def test_build_is_deterministic_across_timezones(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = root / "scripts" / "build_zipapps.py"
        hashes = []
        with tempfile.TemporaryDirectory() as directory:
            for index, timezone in enumerate(("UTC", "Europe/Berlin", "America/Los_Angeles")):
                output = Path(directory) / str(index)
                env = dict(os.environ, TZ=timezone)
                completed = subprocess.run(
                    [sys.executable, str(script), "--output-dir", str(output)],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                hashes.append(
                    [
                        hashlib.sha256((output / name).read_bytes()).hexdigest()
                        for name in ("hlink", "hlink-server", "hlink-admin")
                    ]
                )
        self.assertEqual(hashes[0], hashes[1])
        self.assertEqual(hashes[1], hashes[2])


if __name__ == "__main__":
    unittest.main()
