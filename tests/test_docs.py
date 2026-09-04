from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
GUIDE = ROOT / "docs" / "OMARCHY_REMOTE_HERMES.md"
SECURITY = ROOT / "SECURITY.md"


class DocumentationTests(unittest.TestCase):
    def test_public_readme_links_complete_setup_guide(self) -> None:
        text = README.read_text(encoding="utf-8")
        self.assertIn("docs/OMARCHY_REMOTE_HERMES.md", text)
        self.assertIn("https://github.com/kzndk/hlink", text)
        self.assertIn("public pre-alpha and fail-closed", text)

    def test_setup_guide_covers_install_diagnose_use_and_revoke(self) -> None:
        text = GUIDE.read_text(encoding="utf-8")
        required = (
            "studio-prepare",
            "omarchy-install",
            "studio-finish",
            "hlink --doctor",
            "hlink --continue",
            "hlink --resume",
            "revoke --side studio",
            "revoke --side omarchy",
            "HERMES_LINK_CONTROL_VERSION",
            "TERMINAL_SYNC_REMOTE_STATE=false",
        )
        for value in required:
            with self.subTest(value=value):
                self.assertIn(value, text)
        self.assertIn("venv/bin/hermes", text)
        self.assertNotIn("--hermes-command", text)
        self.assertIn("future Omarchy authentication", text)
        self.assertIn("do **not** terminate already-authenticated", text)

    def test_relative_markdown_links_resolve(self) -> None:
        for source in (README, GUIDE, SECURITY):
            text = source.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
                if target.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                path = (source.parent / target.split("#", 1)[0]).resolve()
                with self.subTest(source=source.name, target=target):
                    self.assertTrue(path.exists(), f"broken link: {source} -> {target}")

    def test_public_docs_do_not_contain_private_machine_examples(self) -> None:
        text = "\n".join(
            path.read_text(encoding="utf-8") for path in (README, GUIDE, SECURITY)
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
