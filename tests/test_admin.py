import base64
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

from hermachy.admin import (
    AdminError,
    _quote_authorized_command,
    atomic_write,
    ensure_ed25519_key,
    install_executable,
    main,
    normalize_ed25519_public_key,
    remove_marked_block,
    upsert_marked_block,
)


def valid_public_key(comment: str = "test") -> str:
    key_type = b"ssh-ed25519"
    public = b"x" * 32
    blob = struct.pack(">I", len(key_type)) + key_type + struct.pack(">I", len(public)) + public
    return f"ssh-ed25519 {base64.b64encode(blob).decode('ascii')} {comment}"


class AdminTests(unittest.TestCase):
    def test_atomic_write_sets_exact_mode_and_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "file"
            atomic_write(path, b"secret\n", 0o600)
            self.assertEqual(path.read_bytes(), b"secret\n")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_install_executable_sets_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.write_bytes(b"#!/usr/bin/env python3\n")
            destination = root / "bin" / "hermachy"
            install_executable(source, destination)
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o755)

    def test_marked_block_is_idempotent_and_preserves_unrelated_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "authorized_keys"
            unrelated = "ssh-ed25519 AAAA unrelated\n# keep this\n"
            path.write_text(unrelated, encoding="utf-8")
            upsert_marked_block(path, "executor", ["restrict " + valid_public_key()], 0o600)
            first = path.read_text(encoding="utf-8")
            upsert_marked_block(path, "executor", ["restrict " + valid_public_key()], 0o600)
            second = path.read_text(encoding="utf-8")
            self.assertEqual(first, second)
            self.assertTrue(second.startswith(unrelated))
            self.assertEqual(second.count("# >>> hermachy:executor >>>"), 1)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            lock_path = path.with_name(path.name + ".hermachy.lock")
            self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode), 0o600)

    def test_marked_block_replacement_changes_only_managed_region(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file"
            upsert_marked_block(path, "x", ["old"], 0o600)
            path.write_text("before\n" + path.read_text() + "after\n", encoding="utf-8")
            upsert_marked_block(path, "x", ["new"], 0o600)
            self.assertEqual(
                path.read_text(),
                "before\n# >>> hermachy:x >>>\nnew\n# <<< hermachy:x <<<\nafter\n",
            )
            self.assertTrue(remove_marked_block(path, "x", 0o600))
            self.assertEqual(path.read_text(), "before\nafter\n")
            self.assertFalse(remove_marked_block(path, "x", 0o600))

    def test_rejects_malformed_marked_regions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file"
            path.write_text("# >>> hermachy:x >>>\nno end\n", encoding="utf-8")
            with self.assertRaises(AdminError):
                upsert_marked_block(path, "x", ["new"], 0o600)

    def test_managed_update_rejects_concurrent_external_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "authorized_keys"
            with mock.patch(
                "hermachy.admin._read_managed_text",
                side_effect=["ssh-ed25519 AAAA original\n", "ssh-ed25519 AAAA changed\n"],
            ), self.assertRaisesRegex(AdminError, "changed concurrently"):
                upsert_marked_block(path, "x", ["managed"], 0o600)

    def test_validates_and_normalizes_ed25519_public_key(self) -> None:
        normalized = normalize_ed25519_public_key(valid_public_key("old"), "new-comment")
        self.assertTrue(normalized.startswith("ssh-ed25519 "))
        self.assertTrue(normalized.endswith(" new-comment"))
        for bad in ["", "ssh-rsa AAAA x", "ssh-ed25519 !!!! x", "ssh-ed25519 eA== x"]:
            with self.subTest(bad=bad), self.assertRaises(AdminError):
                normalize_ed25519_public_key(bad, "new")

    def test_existing_private_and_public_keys_must_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private, public = ensure_ed25519_key(root / "first", "first")
            _, unrelated_public = ensure_ed25519_key(root / "second", "second")
            public.write_bytes(unrelated_public.read_bytes())
            with self.assertRaisesRegex(AdminError, "mismatch"):
                ensure_ed25519_key(private, "first")

    def test_authorized_command_shell_quotes_metacharacter_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "hermachy server; printf INJECTED"
            executable.write_text("#!/bin/sh\nprintf 'SAFE\\n'\n", encoding="utf-8")
            executable.chmod(0o700)
            command = _quote_authorized_command(executable)
            completed = subprocess.run(
                ["/bin/sh", "-c", command], capture_output=True, text=True, check=False
            )
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stdout, "SAFE\n")
            self.assertNotIn("INJECTED", completed.stdout)

    def test_studio_prepare_generates_key_and_handoff_public_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            handoff = Path(directory) / "handoff"
            admin_artifact = Path(directory) / "hermachy-admin"
            admin_artifact.write_bytes(b"#!/usr/bin/env python3\n")
            output = io.StringIO()
            with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=False), mock.patch(
                "sys.stdout", output
            ):
                result = main([
                    "studio-prepare", "--output-dir", str(handoff),
                    "--admin-artifact", str(admin_artifact),
                ])
            self.assertEqual(result, 0)
            report = json.loads(output.getvalue())
            private = home / ".ssh" / "hermachy_to_omarchy"
            self.assertTrue(private.is_file())
            self.assertTrue(private.with_name(private.name + ".pub").is_file())
            self.assertTrue((handoff / "studio-executor.pub").is_file())
            self.assertTrue((home / ".local" / "bin" / "hermachy-admin").is_file())
            self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o600)
            self.assertEqual(report["status"], "prepared")

    def test_omarchy_install_preserves_keys_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            home.mkdir()
            artifact = root / "hermachy"
            artifact.write_bytes(b"#!/usr/bin/env python3\nprint('hermachy')\n")
            (root / "hermachy-exec").write_bytes(b"#!/usr/bin/env python3\n")
            (root / "tui" / "scripts").mkdir(parents=True)
            (root / "tui/entry.mjs").write_text('// native TUI fixture\n')
            (root / "tui/scripts/clipboard_image.py").write_text('# helper\n')
            stale = home / ".local" / "bin" / "hermachy-gateway"
            stale.parent.mkdir(parents=True)
            stale.write_bytes(b"0.5 stdio relay")
            studio_pub = root / "studio.pub"
            studio_pub.write_text(valid_public_key("studio"), encoding="utf-8")
            ssh_dir = home / ".ssh"
            ssh_dir.mkdir()
            authorized = ssh_dir / "authorized_keys"
            authorized.write_text("ssh-ed25519 AAAA unrelated\n", encoding="utf-8")
            args = [
                "omarchy-install",
                "--artifact", str(artifact),
                "--studio-host", "morpheus.example-tailnet.ts.net",
                "--studio-user", "studio-user",
                "--studio-executor-public-key", str(studio_pub),
                "--admin-artifact", str(artifact),
            ]
            with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=False):
                self.assertEqual(main(args), 0)
                first = authorized.read_text()
                self.assertEqual(main(args), 0)
            self.assertEqual(first, authorized.read_text())
            self.assertIn("unrelated", first)
            self.assertIn('restrict,command="', first)
            self.assertIn('hermachy-exec" ssh-ed25519', first)
            self.assertTrue((home / ".local" / "bin" / "hermachy").is_file())
            self.assertTrue((home / ".local" / "bin" / "hermachy-admin").is_file())
            self.assertFalse(stale.exists())
            self.assertTrue((home / ".local/share/hermachy/tui/scripts/clipboard_image.py").is_file())
            self.assertTrue((home / ".local" / "bin" / "hermachy-exec").is_file())
            self.assertTrue((home / ".local/share/hermachy/tui/entry.mjs").is_file())
            self.assertFalse((home / ".local" / "bin" / "hermachy").is_symlink())
            config_path = home / ".config" / "hermachy" / "client.json"
            config = json.loads(config_path.read_text())
            self.assertEqual(config["studio_host"], "morpheus.example-tailnet.ts.net")
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
            self.assertTrue((home / ".ssh" / "hermachy_to_morpheus.pub").is_file())

    def test_incomplete_release_does_not_replace_working_client(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / 'home'
            target = home / '.local/bin/hermachy'
            target.parent.mkdir(parents=True)
            target.write_bytes(b'working client')
            artifact = root / 'hermachy'
            artifact.write_bytes(b'new client')
            args = ['omarchy-install', '--artifact', str(artifact),
                    '--studio-host', 'host', '--studio-user', 'user',
                    '--studio-executor-public-key', str(root / 'unused.pub')]
            with mock.patch.dict(os.environ, {'HOME': str(home)}):
                self.assertEqual(main(args), 2)
            self.assertEqual(target.read_bytes(), b'working client')

    def studio_fixture(self, root: Path):
        home = root / "home"
        home.mkdir()
        artifact = root / "hermachy-server"
        artifact.write_bytes(b"#!/usr/bin/env python3\n")
        plugin = root / "plugin" / "hermachy"
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text("name: hermachy\nversion: 0.6.0\n")
        (plugin / "__init__.py").write_text("def register(ctx):\n    pass\n")
        ensure_ed25519_key(home / ".ssh" / "hermachy_to_omarchy", "studio-executor")
        client_pub = root / "client.pub"
        client_pub.write_text(valid_public_key("client"), encoding="utf-8")
        hermes_home = home / ".hermes"
        hermes_home.mkdir()
        log = root / "hermes-calls"
        hermes = root / "hermes"
        (hermes_home / "config.yaml").write_text("platform_toolsets:\n  cli:\n    - terminal\n    - hermachy\n"
                                                 "  slack:\n    - hermes-slack\n    - hermachy\n"
                                                 "  discord:\n    - hermes-discord\nother: 1\n")
        hermes.write_text(f"#!/bin/sh\necho \"$*\" >> {log}\n")
        hermes.chmod(0o755)
        args = ["studio-finish", "--artifact", str(artifact),
                "--executor-host", "omarchy.example-tailnet.ts.net", "--executor-user", "omarchy-user",
                "--client-public-key", str(client_pub), "--hermes-command", str(hermes),
                "--hermes-home", str(hermes_home)]
        return home, hermes_home, hermes, log, args

    def test_studio_finish_installs_server_plugin_and_forced_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, hermes_home, hermes, log, args = self.studio_fixture(Path(directory))
            authorized = home / ".ssh" / "authorized_keys"
            authorized.write_text("ssh-ed25519 AAAA unrelated\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=False):
                self.assertEqual(main(args), 0)
                first = authorized.read_text()
                self.assertEqual(main(args), 0)
            self.assertEqual(first, authorized.read_text())
            self.assertIn("unrelated", first)
            server_path = home / ".local" / "bin" / "hermachy-server"
            self.assertIn(f'restrict,command="{server_path}"', first)
            self.assertNotIn("pty", first)
            config_path = home / ".config" / "hermachy" / "server.json"
            config = json.loads(config_path.read_text())
            self.assertEqual(config["hermes_command"], str(hermes))
            self.assertEqual(config["hermes_home"], str(hermes_home))
            self.assertNotIn("hermes_source_root", config)
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
            self.assertTrue((hermes_home / "plugins" / "hermachy" / "__init__.py").is_file())
            for sub in ("", "connections", "sessions", "workspaces"):
                self.assertEqual(stat.S_IMODE((hermes_home / "hermachy" / sub).stat().st_mode), 0o700)
            calls = log.read_text()
            self.assertIn("--profile default plugins enable hermachy --no-allow-tool-override", calls)
            self.assertIn("--profile default tools disable hermachy --platform slack", calls)
            self.assertNotIn("--platform cli", calls)
            self.assertNotIn("--platform discord", calls)

    def test_studio_finish_can_install_without_enabling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, hermes_home, _, log, args = self.studio_fixture(Path(directory))
            with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=False):
                self.assertEqual(main(args + ["--no-enable"]), 0)
            self.assertFalse(log.exists())

    def test_studio_finish_rejects_a_missing_hermes_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, _, hermes, _, args = self.studio_fixture(Path(directory))
            hermes.unlink()
            installed = home / ".local/bin/hermachy-server"
            installed.parent.mkdir(parents=True)
            installed.write_bytes(b"working server")
            with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=False):
                self.assertEqual(main(args), 2)
            self.assertEqual(installed.read_bytes(), b"working server")


if __name__ == "__main__":
    unittest.main()
