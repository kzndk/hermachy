import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from hermes_link.protocol import LaunchRequest, encode_request
from hermes_link.server import (
    ServerConfig,
    ServerError,
    build_executor_ssh_argv,
    build_hermes_argv,
    build_hermes_env,
    load_server_config,
    main,
    run_doctor,
    verify_safe_executor_capability,
    SAFE_EXECUTOR_CAPABILITY_VERSION,
)


class ServerTests(unittest.TestCase):
    def make_config(self, root: Path, mode: int = 0o600) -> tuple[Path, ServerConfig]:
        key = root / "id_ed25519"
        key.write_text("private", encoding="utf-8")
        key.chmod(0o600)
        source_root = root / "hermes-agent"
        runtime_python = source_root / "venv" / "bin" / "python"
        runtime_python.parent.mkdir(parents=True)
        runtime_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        runtime_python.chmod(0o700)
        hermes = runtime_python.parent / "hermes"
        hermes.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hermes.chmod(0o700)
        payload = {
            "hermes_command": str(hermes),
            "hermes_source_root": str(source_root),
            "executor_host": "omarchy.example-tailnet.ts.net",
            "executor_user": "omarchy-user",
            "executor_identity_file": str(key),
            "executor_port": 22,
        }
        path = root / "server.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(mode)
        return path, ServerConfig(
            hermes, payload["executor_host"], payload["executor_user"], key, 22, source_root
        )

    def test_loads_private_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, expected = self.make_config(Path(directory))
            self.assertEqual(load_server_config(path), expected)

    def test_rejects_command_not_bound_to_probed_hermes_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = self.make_config(root)
            unrelated = root / "unrelated-hermes"
            unrelated.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            unrelated.chmod(0o700)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["hermes_command"] = str(unrelated)
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ServerError, "same Hermes runtime"):
                load_server_config(path)

    def test_rejects_config_with_group_or_other_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, _ = self.make_config(Path(directory), mode=0o644)
            with self.assertRaisesRegex(ServerError, "permissions"):
                load_server_config(path)

    def test_rejects_missing_unknown_and_invalid_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = self.make_config(root)
            payload = json.loads(path.read_text())
            cases = [
                {key: value for key, value in payload.items() if key != "executor_user"},
                {**payload, "extra": True},
                {**payload, "executor_host": "bad host"},
                {**payload, "executor_port": 0},
                {**payload, "hermes_command": "relative"},
            ]
            for index, candidate in enumerate(cases):
                candidate_path = root / f"bad-{index}.json"
                candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
                candidate_path.chmod(0o600)
                with self.subTest(candidate=candidate), self.assertRaises(ServerError):
                    load_server_config(candidate_path)

    def test_builds_process_scoped_hermes_environment(self) -> None:
        config = ServerConfig(
            Path("/opt/hermes"), "omarchy.tail", "omarchy-user", Path("/keys/executor"), 2222
        )
        env = build_hermes_env(
            config,
            LaunchRequest("/home/omarchy-user/project"),
            base_env={"HOME": "/Users/studio-user", "PYTHONPATH": "bad", "PYTHONHOME": "bad"},
        )
        self.assertEqual(env["HOME"], "/Users/studio-user")
        self.assertEqual(env["HERMES_TUI"], "1")
        self.assertEqual(env["TERMINAL_ENV"], "ssh")
        self.assertEqual(env["TERMINAL_CWD"], "/home/omarchy-user/project")
        self.assertEqual(env["TERMINAL_SSH_HOST"], "omarchy.tail")
        self.assertEqual(env["TERMINAL_SSH_USER"], "omarchy-user")
        self.assertEqual(env["TERMINAL_SSH_PORT"], "2222")
        self.assertEqual(env["TERMINAL_SSH_KEY"], "/keys/executor")
        self.assertEqual(env["TERMINAL_SSH_PERSISTENT"], "true")
        self.assertEqual(env["TERMINAL_SYNC_REMOTE_STATE"], "false")
        self.assertEqual(env["HLINK_REQUIRE_SAFE_EXECUTOR"], "1")
        self.assertEqual(env["PYTHONPATH"], "/opt/hermes-agent")
        self.assertNotIn("PYTHONHOME", env)

    def test_builds_hermes_argv_for_each_action(self) -> None:
        config = ServerConfig(Path("/h"), "host", "user", Path("/key"))
        cases = [
            (
                LaunchRequest("/p"),
                [
                    "/opt/hermes-agent/venv/bin/python",
                    "-c",
                    mock.ANY,
                    "--profile=default",
                    "--tui",
                    "--no-restore-cwd",
                ],
            ),
            (
                LaunchRequest("/p", "continue"),
                [
                    "/opt/hermes-agent/venv/bin/python",
                    "-c",
                    mock.ANY,
                    "--profile=default",
                    "--tui",
                    "--no-restore-cwd",
                    "--continue",
                ],
            ),
            (
                LaunchRequest("/p", "continue", "name"),
                [
                    "/opt/hermes-agent/venv/bin/python",
                    "-c",
                    mock.ANY,
                    "--profile=default",
                    "--tui",
                    "--no-restore-cwd",
                    "--continue=name",
                ],
            ),
            (
                LaunchRequest("/p", "resume", "id"),
                [
                    "/opt/hermes-agent/venv/bin/python",
                    "-c",
                    mock.ANY,
                    "--profile=default",
                    "--tui",
                    "--no-restore-cwd",
                    "--resume=id",
                ],
            ),
        ]
        for request, expected in cases:
            with self.subTest(request=request):
                self.assertEqual(build_hermes_argv(config, request), expected)

    def test_executor_ssh_argv_is_fixed_and_quotes_no_client_shell(self) -> None:
        config = ServerConfig(Path("/h"), "host", "user", Path("/key"), 2200)
        argv = build_executor_ssh_argv(config, "/home/u/a; touch /tmp/pwned")
        self.assertEqual(argv[:4], ["ssh", "-T", "-F", "/dev/null"])
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertNotIn("StrictHostKeyChecking=accept-new", argv)
        self.assertEqual(argv[-2], "user@host")
        self.assertNotIn("touch /tmp/pwned", argv[-1])
        self.assertIn("python3 -c", argv[-1])

    def test_doctor_reports_both_sides(self) -> None:
        config = ServerConfig(Path("/h"), "host", "omarchy-user", Path("/key"))
        executor = {"hostname": "omarchy", "user": "omarchy-user", "cwd": "/home/u/p", "system": "Linux"}
        completions = [
            subprocess.CompletedProcess([], 0, "Hermes Agent v0.21.0\n", ""),
            subprocess.CompletedProcess([], 0, json.dumps(executor) + "\n", ""),
        ]
        output = io.StringIO()
        with mock.patch("hermes_link.server.subprocess.run", side_effect=completions), mock.patch(
            "hermes_link.server.socket.gethostname", return_value="Morpheus"
        ), mock.patch("hermes_link.server.getpass.getuser", return_value="studio-user"), mock.patch(
            "sys.stdout", output
        ):
            result = run_doctor(config, LaunchRequest("/home/u/p", "doctor"))
        self.assertEqual(result, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["studio"]["hostname"], "Morpheus")
        self.assertEqual(report["executor"], executor)
        self.assertNotIn("identity", json.dumps(report).lower())

    def test_safe_executor_capability_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, config = self.make_config(Path(directory))
            del path
            failures = [
                subprocess.CompletedProcess([], 1, "", "missing"),
                subprocess.CompletedProcess([], 0, "1\n", ""),
                subprocess.CompletedProcess([], 0, "999\n", ""),
                subprocess.CompletedProcess([], 1, SAFE_EXECUTOR_CAPABILITY_VERSION, "failed import"),
            ]
            for completed in failures:
                with self.subTest(completed=completed), mock.patch(
                    "hermes_link.server.subprocess.run", return_value=completed
                ), self.assertRaisesRegex(ServerError, "safe-executor"):
                    verify_safe_executor_capability(config)

            with mock.patch(
                "hermes_link.server.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, SAFE_EXECUTOR_CAPABILITY_VERSION + "\n", ""),
            ) as capability_probe:
                verify_safe_executor_capability(config)
            self.assertEqual(
                capability_probe.call_args.args[0][0],
                str(config.hermes_source_root / "venv" / "bin" / "python"),
            )
            self.assertEqual(
                capability_probe.call_args.kwargs["env"]["PYTHONPATH"],
                str(config.hermes_source_root),
            )
            self.assertNotIn("PYTHONHOME", capability_probe.call_args.kwargs["env"])

    def test_doctor_rejects_mismatched_executor_identity(self) -> None:
        config = ServerConfig(Path("/h"), "host", "omarchy-user", Path("/key"))
        bad_reports = [
            {"hostname": "o", "user": "other", "cwd": "/p", "system": "Linux"},
            {"hostname": "o", "user": "omarchy-user", "cwd": "/wrong", "system": "Linux"},
            {"hostname": "o", "user": "omarchy-user", "cwd": "/p", "system": "Darwin"},
            {"hostname": "o", "user": "omarchy-user", "cwd": "/p"},
        ]
        for report in bad_reports:
            completions = [
                subprocess.CompletedProcess([], 0, "Hermes Agent v0.21.0\n", ""),
                subprocess.CompletedProcess([], 0, json.dumps(report), ""),
            ]
            output = io.StringIO()
            with self.subTest(report=report), mock.patch(
                "hermes_link.server.subprocess.run", side_effect=completions
            ), mock.patch("sys.stdout", output):
                self.assertEqual(run_doctor(config, LaunchRequest("/p", "doctor")), 1)
            self.assertEqual(json.loads(output.getvalue())["status"], "error")

    def test_main_requires_forced_command(self) -> None:
        error = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("sys.stderr", error):
            self.assertEqual(main([]), 2)
        self.assertIn("expected", error.getvalue())

    def test_main_rejects_interactive_launch_without_tty(self) -> None:
        request = LaunchRequest("/home/u/p")
        with tempfile.TemporaryDirectory() as directory:
            path, _ = self.make_config(Path(directory))
            env = {
                "SSH_ORIGINAL_COMMAND": f"hlink-v1 {encode_request(request)}",
                "HLINK_SERVER_CONFIG": str(path),
            }
            with mock.patch.dict(os.environ, env, clear=True), mock.patch(
                "hermes_link.server.has_interactive_tty", return_value=False
            ), mock.patch(
                "hermes_link.server.verify_safe_executor_capability", return_value=None
            ):
                self.assertEqual(main([]), 2)

    def test_main_execs_hermes_without_a_shell(self) -> None:
        request = LaunchRequest("/home/u/p", "continue", "name")
        with tempfile.TemporaryDirectory() as directory:
            path, config = self.make_config(Path(directory))
            env = {
                "SSH_ORIGINAL_COMMAND": f"hlink-v1 {encode_request(request)}",
                "HLINK_SERVER_CONFIG": str(path),
                "HOME": "/Users/u",
            }
            with mock.patch.dict(os.environ, env, clear=True), mock.patch(
                "hermes_link.server.has_interactive_tty", return_value=True
            ), mock.patch(
                "hermes_link.server.verify_safe_executor_capability", return_value=None
            ), mock.patch("hermes_link.server.os.execvpe", side_effect=RuntimeError("stop")) as execute:
                with self.assertRaisesRegex(RuntimeError, "stop"):
                    main([])
        argv = execute.call_args.args[1]
        child_env = execute.call_args.args[2]
        self.assertEqual(argv, build_hermes_argv(config, request))
        self.assertEqual(
            execute.call_args.args[0],
            str(config.hermes_source_root / "venv" / "bin" / "python"),
        )
        self.assertEqual(child_env["TERMINAL_CWD"], request.cwd)
        self.assertEqual(child_env["PYTHONPATH"], str(config.hermes_source_root))


if __name__ == "__main__":
    unittest.main()
