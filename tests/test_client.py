import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from hermes_link.client import (
    ClientConfig,
    ClientError,
    build_request,
    build_ssh_argv,
    load_client_config,
    main,
    parse_args,
)
from hermes_link.protocol import decode_request


class ClientTests(unittest.TestCase):
    def write_config(self, root: Path, **overrides: object) -> Path:
        identity = root / "id_ed25519"
        identity.write_text("test private key", encoding="utf-8")
        payload: dict[str, object] = {
            "studio_host": "morpheus.example-tailnet.ts.net",
            "studio_user": "studio-user",
            "identity_file": str(identity),
            "studio_port": 22,
        }
        payload.update(overrides)
        path = root / "client.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_loads_config_and_expands_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.dict(os.environ, {"HOME": str(root)}):
                key = root / ".ssh" / "hlink_key"
                key.parent.mkdir()
                key.write_text("key", encoding="utf-8")
                path = self.write_config(root, identity_file="~/.ssh/hlink_key")
                config = load_client_config(path)
        self.assertEqual(config.identity_file, key)
        self.assertEqual(config.studio_port, 22)

    def test_rejects_missing_unknown_or_invalid_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = [
                {"studio_host": "host", "identity_file": "/tmp/key"},
                {
                    "studio_host": "host",
                    "studio_user": "user",
                    "identity_file": "/tmp/key",
                    "unexpected": True,
                },
                {
                    "studio_host": "bad\nhost",
                    "studio_user": "user",
                    "identity_file": "/tmp/key",
                },
                {
                    "studio_host": "host",
                    "studio_user": "user",
                    "identity_file": "/tmp/key",
                    "studio_port": 70000,
                },
            ]
            for index, payload in enumerate(cases):
                path = root / f"bad-{index}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.subTest(payload=payload), self.assertRaises(ClientError):
                    load_client_config(path)

    def test_builds_exact_ssh_argv(self) -> None:
        config = ClientConfig(
            studio_host="morpheus.example-tailnet.ts.net",
            studio_user="studio-user",
            identity_file=Path("/home/u/.ssh/hlink"),
            studio_port=2222,
        )
        request = build_request(parse_args(["--resume", "abc"]), Path("/home/u/p"))
        argv = build_ssh_argv(config, request)
        self.assertEqual(argv[:4], ["ssh", "-tt", "-F", "/dev/null"])
        self.assertIn("IdentitiesOnly=yes", argv)
        self.assertIn("StrictHostKeyChecking=accept-new", argv)
        self.assertIn("ConnectTimeout=8", argv)
        self.assertIn("ServerAliveInterval=15", argv)
        self.assertIn("ServerAliveCountMax=3", argv)
        self.assertEqual(argv[-2], "studio-user@morpheus.example-tailnet.ts.net")
        self.assertTrue(argv[-1].startswith("hlink-v1 "))
        decoded = decode_request(argv[-1].split(" ", 1)[1])
        self.assertEqual(decoded.cwd, "/home/u/p")
        self.assertEqual(decoded.action, "resume")
        self.assertEqual(decoded.session, "abc")

    def test_action_parsing(self) -> None:
        cwd = Path("/home/u/project")
        cases = [
            ([], ("new", None)),
            (["--continue"], ("continue", None)),
            (["-c", "named"], ("continue", "named")),
            (["--resume", "id"], ("resume", "id")),
            (["--doctor"], ("doctor", None)),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                request = build_request(parse_args(argv), cwd)
                self.assertEqual((request.action, request.session), expected)

    def test_mutually_exclusive_actions(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args(["--continue", "--resume", "id"])

    def test_cwd_override_is_resolved_and_must_be_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            request = build_request(parse_args(["--cwd", directory]), Path("/elsewhere"))
            self.assertEqual(request.cwd, os.path.abspath(directory))
            missing = path / "missing"
            with self.assertRaises(ClientError):
                build_request(parse_args(["--cwd", str(missing)]), Path("/elsewhere"))

    def test_main_runs_ssh_and_returns_its_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = self.write_config(root)
            with mock.patch.dict(
                os.environ, {"HLINK_CLIENT_CONFIG": str(config_path)}, clear=False
            ), mock.patch("hermes_link.client.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 17)
                result = main(["--doctor", "--cwd", directory])
        self.assertEqual(result, 17)
        called_argv = run.call_args.args[0]
        request = decode_request(called_argv[-1].split(" ", 1)[1])
        self.assertEqual(request.action, "doctor")
        self.assertFalse(run.call_args.kwargs["check"])

    def test_main_fails_before_ssh_when_identity_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = self.write_config(root)
            Path(json.loads(config_path.read_text())["identity_file"]).unlink()
            with mock.patch.dict(
                os.environ, {"HLINK_CLIENT_CONFIG": str(config_path)}, clear=False
            ), mock.patch("hermes_link.client.subprocess.run") as run:
                result = main(["--cwd", directory])
        self.assertEqual(result, 2)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
