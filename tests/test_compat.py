import hashlib
import json
from pathlib import Path
import runpy
import tempfile
import unittest


HELPER = runpy.run_path(str(Path(__file__).resolve().parents[1] / "compat/hermes/prepare.py"))
prepare = HELPER["prepare"]
verify_source = HELPER["verify_source"]
git = HELPER["git"]


class CompatibilityPreparationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        origin = self.root / "public origin"
        origin.mkdir()
        git(origin, "init", "--quiet")
        (origin / "runtime.txt").write_text("base\n")
        git(origin, "add", "runtime.txt")
        git(origin, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "base")
        revision = git(origin, "rev-parse", "HEAD")
        (origin / "runtime.txt").write_text("compatible\n")
        git(origin, "add", "runtime.txt")
        patch = (git(origin, "diff", "--cached", "--full-index") + "\n").encode()
        self.manifest = {
            "repository": str(origin), "revision": revision,
            "patched_tree": git(origin, "write-tree"),
            "patch_sha256": hashlib.sha256(patch).hexdigest(),
        }
        self.manifest_path = self.root / "manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest))
        (self.root / "safe-executor-v2.patch").write_bytes(patch)
        self.destination = self.root / "new runtime"

    def test_fetch_apply_verify_and_detect_working_and_index_drift(self):
        prepare(self.destination, self.manifest_path)
        runtime = self.destination / "runtime.txt"
        self.assertEqual(runtime.read_text(), "compatible\n")
        verify_source(self.destination, self.manifest)
        runtime.write_text("changed\n")
        with self.assertRaises(ValueError):
            verify_source(self.destination, self.manifest)
        git(self.destination, "add", "runtime.txt")
        with self.assertRaisesRegex(ValueError, "patch differs"):
            verify_source(self.destination, self.manifest)

    def test_refuses_existing_destination_and_preserves_content(self):
        self.destination.mkdir()
        sentinel = self.destination / "keep.txt"
        sentinel.write_text("existing installation")
        with self.assertRaisesRegex(ValueError, "already exists"):
            prepare(self.destination, self.manifest_path)
        self.assertEqual(sentinel.read_text(), "existing installation")

    def test_tampered_patch_is_rejected_before_creating_destination(self):
        (self.root / "safe-executor-v2.patch").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            prepare(self.destination, self.manifest_path)
        self.assertFalse(self.destination.exists())

    def test_wrong_result_tree_is_rejected(self):
        self.manifest["patched_tree"] = git(Path(self.manifest["repository"]), "rev-parse", "HEAD^{tree}")
        self.manifest_path.write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(ValueError, "patch differs"):
            prepare(self.destination, self.manifest_path)

    def test_wrong_revision_and_extra_files_are_rejected(self):
        prepare(self.destination, self.manifest_path)
        with self.assertRaisesRegex(ValueError, "revision differs"):
            verify_source(self.destination, dict(self.manifest, revision="0" * 40))
        (self.destination / "unexpected.py").write_text("pass\n")
        with self.assertRaisesRegex(ValueError, "untracked files"):
            verify_source(self.destination, self.manifest)
