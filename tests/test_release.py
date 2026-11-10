import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from scripts.build_release import build_release


class ReleaseTests(unittest.TestCase):
    def test_bundle_is_complete_reproducible_and_detects_stale_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "checksums.json"
            archive = build_release(root / "first", manifest)
            second = build_release(root / "second", manifest, check=True)
            self.assertEqual(archive.read_bytes(), second.read_bytes())
            hashes = json.loads(manifest.read_text())
            with tarfile.open(archive) as bundle:
                self.assertEqual(set(bundle.getnames()), set(hashes) | {
                    "LICENSE", "release-sha256.json", "README.md", "CHANGELOG.md", "SECURITY.md",
                    "docs/OMARCHY_REMOTE_HERMES.md",
                })
                for name in ("plugin/hermachy/__init__.py", "plugin/hermachy/plugin.yaml",
                             "tui/scripts/clipboard_image.py", "hermachy-exec"):
                    self.assertIn(name, hashes)
                self.assertNotIn("hermachy-gateway", hashes)
                for name, digest in hashes.items():
                    self.assertEqual(hashlib.sha256(bundle.extractfile(name).read()).hexdigest(), digest)
                self.assertEqual(bundle.getmember("hermachy").mode, 0o755)
            expected = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.assertEqual((archive.parent / "SHA256SUMS").read_text(), f"{expected}  {archive.name}\n")
            manifest.write_text('{}')
            with self.assertRaisesRegex(ValueError, "checksums are stale"):
                build_release(root / "second", manifest, check=True)
