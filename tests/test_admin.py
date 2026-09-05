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

from hermes_link.admin import (
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
            destination = root / "bin" / "hlink"
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
            self.assertEqual(second.count("# >>> hermes-link:executor >>>"), 1)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            lock_path = path.with_name(path.name + ".hlink.lock")
            self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode), 0o600)

    def test_marked_block_replacement_changes_only_managed_region(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file"
            upsert_marked_block(path, "x", ["old"], 0o600)
            path.write_text("before\n" + path.read_text() + "after\n", encoding="utf-8")
            upsert_marked_block(path, "x", ["new"], 0o600)
            self.assertEqual(
                path.read_text(),
                "before\n# >>> hermes-link:x >>>\nnew\n# <<< hermes-link:x <<<\nafter\n",
            )
            self.assertTrue(remove_marked_block(path, "x", 0o600))
            self.assertEqual(path.read_text(), "before\nafter\n")
            self.assertFalse(remove_marked_block(path, "x", 0o600))

    def test_rejects_malformed_marked_regions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file"
            path.write_text("# >>> hermes-link:x >>>\nno end\n", encoding="utf-8")
            with self.assertRaises(AdminError):
                upsert_marked_block(path, "x", ["new"], 0o600)

    def test_managed_update_rejects_concurrent_external_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "authorized_keys"
            with mock.patch(
                "hermes_link.admin._read_managed_text",
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
            executable = Path(directory) / "hlink server; printf INJECTED"
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
            admin_artifact = Path(directory) / "hlink-admin"
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
            private = home / ".ssh" / "hlink_to_omarchy"
            self.assertTrue(private.is_file())
            self.assertTrue(private.with_name(private.name + ".pub").is_file())
            self.assertTrue((handoff / "studio-executor.pub").is_file())
            self.assertTrue((home / ".local" / "bin" / "hlink-admin").is_file())
            self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o600)
            self.assertEqual(report["status"], "prepared")

    def test_omarchy_install_preserves_keys_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            home.mkdir()
            artifact = root / "hlink"
            artifact.write_bytes(b"#!/usr/bin/env python3\nprint('hlink')\n")
            (root / "hlink-gateway").write_bytes(b"#!/usr/bin/env python3\n")
            (root / "tui").mkdir()
            (root / "tui/entry.mjs").write_text('// native TUI fixture\n')
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
            self.assertIn("restrict ssh-ed25519", first)
            self.assertTrue((home / ".local" / "bin" / "hlink").is_file())
            self.assertTrue((home / ".local" / "bin" / "hlink-admin").is_file())
            self.assertTrue((home / ".local" / "bin" / "hlink-gateway").is_file())
            self.assertTrue((home / ".local/share/hlink/tui/entry.mjs").is_file())
            self.assertEqual(
                os.readlink(home / ".local" / "bin" / "hermes-link"), "hlink"
            )
            config_path = home / ".config" / "hermes-link" / "client.json"
            config = json.loads(config_path.read_text())
            self.assertEqual(config["studio_host"], "morpheus.example-tailnet.ts.net")
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
            self.assertTrue((home / ".ssh" / "hlink_to_morpheus.pub").is_file())

    def test_incomplete_release_does_not_replace_working_client(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / 'home'
            target = home / '.local/bin/hlink'
            target.parent.mkdir(parents=True)
            target.write_bytes(b'working client')
            artifact = root / 'hlink'
            artifact.write_bytes(b'new client')
            args = ['omarchy-install', '--artifact', str(artifact),
                    '--studio-host', 'host', '--studio-user', 'user',
                    '--studio-executor-public-key', str(root / 'unused.pub')]
            with mock.patch.dict(os.environ, {'HOME': str(home)}):
                self.assertEqual(main(args), 2)
            self.assertEqual(target.read_bytes(), b'working client')

    def test_studio_finish_installs_forced_command_without_overwriting_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            home.mkdir()
            artifact = root / "hlink-server"
            artifact.write_bytes(b"#!/usr/bin/env python3\n")

            executor_key, _ = ensure_ed25519_key(
                home / ".ssh" / "hlink_to_omarchy", "studio-executor"
            )
            client_pub = root / "client.pub"
            client_pub.write_text(valid_public_key("client"), encoding="utf-8")
            hermes_source_root = root / "hermes-agent"
            runtime_bin = hermes_source_root / "venv" / "bin"
            runtime_bin.mkdir(parents=True)
            hermes = runtime_bin / "hermes"
            hermes.write_bytes(b"#!/bin/sh\n")
            hermes.chmod(0o755)
            runtime_python = runtime_bin / "python"
            runtime_python.write_bytes(b"#!/bin/sh\n")
            runtime_python.chmod(0o755)
            authorized = home / ".ssh" / "authorized_keys"
            authorized.write_text("ssh-ed25519 AAAA unrelated\n", encoding="utf-8")
            args = [
                "studio-finish",
                "--artifact", str(artifact),
                "--executor-host", "omarchy.example-tailnet.ts.net",
                "--executor-user", "omarchy-user",
                "--client-public-key", str(client_pub),
                "--hermes-source-root", str(hermes_source_root),
            ]
            with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=False):
                self.assertEqual(main(args), 0)
                first = authorized.read_text()
                self.assertEqual(main(args), 0)
            self.assertEqual(first, authorized.read_text())
            self.assertIn("unrelated", first)
            server_path = home / ".local" / "bin" / "hlink-server"
            self.assertIn(f'command="{server_path}"', first)
            self.assertIn("restrict,pty", first)
            config_path = home / ".config" / "hermes-link" / "server.json"
            config = json.loads(config_path.read_text())
            self.assertEqual(config["executor_user"], "omarchy-user")
            self.assertEqual(config["hermes_command"], str(hermes))
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)

    def test_studio_finish_rejects_command_outside_probed_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            home.mkdir()
            artifact = root / "hlink-server"
            artifact.write_bytes(b"#!/usr/bin/env python3\n")
            installed = home / '.local/bin/hlink-server'
            installed.parent.mkdir(parents=True)
            installed.write_bytes(b'working server')
            unrelated_hermes = root / "other-hermes"
            unrelated_hermes.write_bytes(b"#!/bin/sh\n")
            unrelated_hermes.chmod(0o755)
            ensure_ed25519_key(home / ".ssh" / "hlink_to_omarchy", "studio-executor")
            client_pub = root / "client.pub"
            client_pub.write_text(valid_public_key("client"), encoding="utf-8")
            runtime_bin = root / "hermes-agent" / "venv" / "bin"
            runtime_bin.mkdir(parents=True)
            runtime_hermes = runtime_bin / "hermes"
            runtime_hermes.write_bytes(b"#!/bin/sh\n")
            runtime_hermes.chmod(0o755)
            runtime_python = runtime_bin / "python"
            runtime_python.write_bytes(b"#!/bin/sh\n")
            runtime_python.chmod(0o755)
            args = [
                "studio-finish",
                "--artifact", str(artifact),
                "--executor-host", "omarchy.example-tailnet.ts.net",
                "--executor-user", "omarchy-user",
                "--client-public-key", str(client_pub),
                "--hermes-command", str(unrelated_hermes),
                "--hermes-source-root", str(root / "hermes-agent"),
            ]

            with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=False), mock.patch(
                "sys.stderr", new_callable=io.StringIO
            ) as error:
                self.assertEqual(main(args), 2)

            self.assertIn("same Hermes runtime", error.getvalue())
            self.assertEqual(installed.read_bytes(), b'working server')
            authorized = home / ".ssh" / "authorized_keys"
            self.assertFalse(authorized.exists())


if __name__ == "__main__":
    unittest.main()
