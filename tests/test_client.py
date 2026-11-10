import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from hermachy.client import (
    ClientConfig,
    ClientError,
    build_request,
    build_ssh_argv,
    load_client_config,
    main,
    parse_args,
    check_remote,
    resolve_session,
    prepare_local_ui,
)
from hermachy.protocol import LaunchRequest, decode_request


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
                key = root / ".ssh" / "hermachy_key"
                key.parent.mkdir()
                key.write_text("key", encoding="utf-8")
                path = self.write_config(root, identity_file="~/.ssh/hermachy_key")
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
            identity_file=Path("/home/u/.ssh/hermachy"),
            studio_port=2222,
        )
        request = build_request(parse_args(["--resume", "abc"]), Path("/home/u/p"))
        argv = build_ssh_argv(config, request)
        self.assertEqual(argv[:4], ["ssh", "-tt", "-F", "/dev/null"])
        self.assertIn("IdentitiesOnly=yes", argv)
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertNotIn("StrictHostKeyChecking=accept-new", argv)
        self.assertIn("ConnectTimeout=8", argv)
        self.assertIn("ServerAliveInterval=15", argv)
        self.assertIn("ServerAliveCountMax=3", argv)
        self.assertEqual(argv[-2], "studio-user@morpheus.example-tailnet.ts.net")
        self.assertTrue(argv[-1].startswith("hermachy-v1 "))
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

    def test_native_scrollback_flag_reaches_local_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry, gateway = root / "entry.mjs", root / "hermachy-gateway"
            entry.touch()
            gateway.touch()
            config = ClientConfig("host", "user", root / "key")
            with mock.patch.dict(os.environ, {"HOME": directory, "HERMES_TUI_NATIVE": "1"}), mock.patch(
                "hermachy.client.ui_paths", return_value=(entry, gateway)
            ), mock.patch("hermachy.client.shutil.which", return_value="/usr/bin/node"):
                for argv, expected in (([], "0"), (["--native"], "1"), (["chat", "--tui-native"], "1")):
                    with self.subTest(argv=argv):
                        _, env = prepare_local_ui(parse_args(argv), config, LaunchRequest(directory, "gateway"))
                        self.assertEqual(env["HERMES_TUI_NATIVE"], expected)

    def test_mutually_exclusive_actions(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args(["--continue", "--resume", "id"])

    def test_unknown_command_does_not_require_a_local_hermes_install(self) -> None:
        with self.assertRaises(SystemExit) as error:
            parse_args(["chta"])
        self.assertEqual(error.exception.code, 2)

    def test_cwd_override_is_resolved_and_must_be_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            request = build_request(parse_args(["--cwd", directory]), Path("/elsewhere"))
            self.assertEqual(request.cwd, os.path.abspath(directory))
            missing = path / "missing"
            with self.assertRaises(ClientError):
                build_request(parse_args(["--cwd", str(missing)]), Path("/elsewhere"))

    def test_main_reports_remote_check_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = self.write_config(root)
            entry = root / "entry.mjs"
            gateway = root / "hermachy-gateway"
            entry.touch()
            gateway.touch()
            with mock.patch.dict(
                os.environ, {"HERMACHY_CLIENT_CONFIG": str(config_path)}, clear=False
            ), mock.patch("hermachy.client.ui_paths", return_value=(entry, gateway)), \
                    mock.patch("hermachy.client.shutil.which", return_value="/usr/bin/node"), \
                    mock.patch("hermachy.client.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 17)
                result = main(["--doctor", "--cwd", directory])
        self.assertEqual(result, 2)
        called_argv = run.call_args.args[0]
        request = decode_request(called_argv[-1].split(" ", 1)[1])
        self.assertEqual(request.action, "doctor")
        self.assertFalse(run.call_args.kwargs["check"])
        self.assertGreater(run.call_args.kwargs["timeout"], 0)

    def test_check_remote_rejects_invalid_success_responses(self) -> None:
        config = ClientConfig('host', 'user', Path('/key'))
        for response in ['null', '[]', '{}', '{"status":"error"}', 'bad json']:
            with self.subTest(response=response), mock.patch(
                'hermachy.client.subprocess.run',
                return_value=subprocess.CompletedProcess([], 0, response, ''),
            ), self.assertRaises(ClientError):
                check_remote(config, '/workspace')

    def test_bad_session_response_is_a_client_error(self) -> None:
        from hermachy.protocol import LaunchRequest
        config = ClientConfig('host', 'user', Path('/key'))
        for response in ['null', '[]', '{}', '{"session":null}']:
            with self.subTest(response=response), mock.patch(
                'hermachy.client.subprocess.run',
                return_value=subprocess.CompletedProcess([], 0, response, ''),
            ), self.assertRaises(ClientError):
                resolve_session(config, LaunchRequest('/workspace', 'resolve'))

    def test_failed_startup_check_does_not_launch_ui(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(Path(directory))
            with mock.patch.dict(os.environ, {'HERMACHY_CLIENT_CONFIG': str(path)}), mock.patch(
                'hermachy.client.check_remote', side_effect=ClientError('incompatible runtime')
            ), mock.patch('hermachy.client.prepare_local_ui') as prepare:
                self.assertEqual(main([]), 2)
                prepare.assert_not_called()

    def test_cwd_symlink_is_canonical_for_executor_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / 'real'
            real.mkdir()
            (root / 'link').symlink_to(real, target_is_directory=True)
            request = build_request(parse_args(['--cwd', str(root / 'link')]), root)
            self.assertEqual(request.cwd, str(real.resolve()))

    def test_main_fails_before_ssh_when_identity_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = self.write_config(root)
            Path(json.loads(config_path.read_text())["identity_file"]).unlink()
            with mock.patch.dict(
                os.environ, {"HERMACHY_CLIENT_CONFIG": str(config_path)}, clear=False
            ), mock.patch("hermachy.client.subprocess.run") as run:
                result = main(["--cwd", directory])
        self.assertEqual(result, 2)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
