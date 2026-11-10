from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
GUIDE = ROOT / "docs" / "OMARCHY_REMOTE_HERMES.md"
SECURITY = ROOT / "SECURITY.md"
CHANGELOG = ROOT / "CHANGELOG.md"
DESIGN = tuple(sorted((ROOT / "docs" / "design").rglob("*.md")))


class DocumentationTests(unittest.TestCase):
    def test_public_readme_links_complete_setup_guide(self) -> None:
        text = README.read_text(encoding="utf-8")
        self.assertIn("docs/OMARCHY_REMOTE_HERMES.md", text)
        self.assertIn("docs/design/SHARED_GATEWAY_0.6.md", text)
        self.assertIn("https://github.com/kzndk/hermachy", text)
        self.assertIn("public pre-alpha and fail-closed", text)

    def test_setup_guide_covers_install_diagnose_use_and_revoke(self) -> None:
        text = GUIDE.read_text(encoding="utf-8")
        required = (
            "studio-prepare",
            "omarchy-install",
            "studio-finish",
            "hermachy --doctor",
            "hermachy --continue",
            "hermachy --resume",
            "revoke --side studio",
            "revoke --side omarchy",
            "plugins enable hermachy",
            "hermes gateway restart",
            "hermachy_ssh",
            "hermes_host",
            "--hermes-command",
        )
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, text)
        # 0.5.x's patched runtime and its installer no longer exist.
        obsolete = (
            "compat/hermes",
            "HERMACHY_CONTROL_VERSION",
            "TERMINAL_SYNC_REMOTE_STATE",
            "venv/bin/hermes",
            "prepare.py",
        )
        for value in obsolete:
            with self.subTest(value=value):
                self.assertNotIn(value, text)
        self.assertIn("future Omarchy authentication", text)
        self.assertIn("do **not** terminate already-authenticated", text)

    def test_relative_markdown_links_resolve(self) -> None:
        for source in (README, GUIDE, SECURITY, CHANGELOG, *DESIGN):
            text = source.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
                if target.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                path = (source.parent / target.split("#", 1)[0]).resolve()
                with self.subTest(source=source.name, target=target):
                    self.assertTrue(path.exists(), f"broken link: {source} -> {target}")

    def test_public_docs_do_not_contain_private_machine_examples(self) -> None:
        text = "\n".join(
            path.read_text(encoding="utf-8") for path in (README, GUIDE, SECURITY, CHANGELOG, *DESIGN)
        )
        forbidden = (
            "/Users/ulrichslothuus",
            "MrSlothuus",
            "Morpheus's",
            "morpheus.example-tailnet.ts.net",
        )
        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, text)


if __name__ == "__main__":
    unittest.main()
