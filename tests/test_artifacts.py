import hashlib
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

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
                (output / "hlink", ["--version"], "Hermes Link 0.1.0"),
                (output / "hlink-server", ["--version"], "Hermes Link server 0.1.0"),
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
            with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "315532800"}):
                first_artifacts = build_all(Path(first))
                second_artifacts = build_all(Path(second))
            first_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in first_artifacts]
            second_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in second_artifacts]
            self.assertEqual(first_hashes, second_hashes)


if __name__ == "__main__":
    unittest.main()
