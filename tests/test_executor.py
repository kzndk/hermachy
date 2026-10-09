import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from hermachy.executor import administrator_argv, desktop_environment, main, run_administrator
from hermachy.server import executor_warnings


class ExecutorTests(unittest.TestCase):
    def test_session_environment_is_allowlisted_and_not_evaluated(self):
        values = {"PATH": "/desktop/bin::relative", "WAYLAND_DISPLAY": "wayland-new",
                  "HYPRLAND_INSTANCE_SIGNATURE": "current", "DISPLAY": ":2",
                  "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
                  "HERMES_API_KEY": "must-not-copy", "BASH_ENV": "/tmp/must-not-source",
                  "XDG_DATA_DIRS": "literal$(printf injection)"}
        with mock.patch("hermachy.executor.subprocess.run", return_value=subprocess.CompletedProcess(
                [], 0, json.dumps(values), "")), mock.patch("hermachy.executor._owned_socket", return_value=True):
            env = desktop_environment({"PATH": "/usr/bin", "SSH_ORIGINAL_COMMAND": "secret command"})
        self.assertNotIn("HERMES_API_KEY", env)
        self.assertNotIn("BASH_ENV", env)
        self.assertNotIn("SSH_ORIGINAL_COMMAND", env)
        self.assertEqual(env["XDG_DATA_DIRS"], values["XDG_DATA_DIRS"])
        self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-new")
        self.assertTrue(all(p.startswith("/") for p in env["PATH"].split(":")))
        self.assertIn(str(Path.home() / ".local/bin"), env["PATH"].split(":"))

    def test_desktop_changes_are_refreshed_and_stale_inherited_values_cleared(self):
        with mock.patch("hermachy.executor._manager_environment", side_effect=[
                {"HYPRLAND_INSTANCE_SIGNATURE": "first", "WAYLAND_DISPLAY": "wayland-1"},
                {"HYPRLAND_INSTANCE_SIGNATURE": "second", "WAYLAND_DISPLAY": "wayland-2"},
                {}]), mock.patch("hermachy.executor._owned_socket", return_value=True), mock.patch(
                "hermachy.executor.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "[]", "")):
            first = desktop_environment({})
            second = desktop_environment(first)
            logged_out = desktop_environment(second)
        self.assertEqual(second["HYPRLAND_INSTANCE_SIGNATURE"], "second")
        self.assertEqual(second["WAYLAND_DISPLAY"], "wayland-2")
        self.assertNotIn("HYPRLAND_INSTANCE_SIGNATURE", logged_out)
        self.assertNotIn("WAYLAND_DISPLAY", logged_out)

    def test_stale_manager_recovers_only_an_unambiguous_live_compositor(self):
        instances = [{"instance": "new", "wl_socket": "wayland-new"}]
        with mock.patch("hermachy.executor._manager_environment", return_value={
                "HYPRLAND_INSTANCE_SIGNATURE": "dead", "WAYLAND_DISPLAY": "wayland-dead"}), mock.patch(
                "hermachy.executor._owned_socket", side_effect=lambda p: "dead" not in str(p)), mock.patch(
                "hermachy.executor.subprocess.run", return_value=subprocess.CompletedProcess(
                    [], 0, json.dumps(instances), "")):
            env = desktop_environment({})
        self.assertEqual(env["HYPRLAND_INSTANCE_SIGNATURE"], "new")
        self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-new")

    def test_headless_shell_retains_input_output_and_exit_code(self):
        # Real process: the forced command must not consume tool stdin or emit
        # startup diagnostics on the byte stream used by file/code tools.
        env = dict(os.environ, SSH_ORIGINAL_COMMAND="cat; exit 37")
        script = "from unittest import mock;from hermachy.executor import main\n" \
                 "with mock.patch('hermachy.executor._manager_environment',return_value={}): main([])"
        result = subprocess.run([sys.executable, "-c", script], input=b"\x00binary\xff\n",
                                env=env, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 37)
        self.assertEqual(result.stdout, b"\x00binary\xff\n")
        self.assertEqual(result.stderr, b"")

    def test_empty_ssh_request_never_opens_a_shell(self):
        with mock.patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": ""}), mock.patch(
                "hermachy.executor.os.execve") as execute:
            self.assertEqual(main([]), 2)
        execute.assert_not_called()

    def test_doctor_distinguishes_transport_from_desktop_access(self):
        self.assertTrue(executor_warnings({}))
        access = {"version": "1", "ssh_entrypoint": True, "desktop": True,
                  "wayland": True, "user_bus": True, "administration": {"available": True}}
        self.assertEqual(executor_warnings({"access": access}), [])
        self.assertIn("log into Hyprland", executor_warnings({"access": dict(access, desktop=False)})[0])

    def test_administration_preserves_argv_and_uses_graphical_authentication(self):
        env = {"PATH": "/usr/bin"}
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
                "hermachy.executor.desktop_environment", return_value=env), mock.patch(
                "hermachy.executor.shutil.which", return_value="/usr/bin/printf"), mock.patch(
                "hermachy.executor.run_administrator", return_value=37) as execute:
            self.assertEqual(main(["--admin", "printf", "%s", "literal; $(touch nothing)"]), 37)
        execute.assert_called_once_with(["/usr/bin/printf", "%s", "literal; $(touch nothing)"], env)
        argv = administrator_argv(execute.call_args.args[0], "test.service")
        self.assertIn("--user", argv)
        self.assertIn("--disable-internal-agent", argv)
        self.assertIn("--keep-cwd", argv)
        self.assertEqual(argv[-3:], ["/usr/bin/printf", "%s", "literal; $(touch nothing)"])

    def test_cancelled_authentication_stops_the_unit_without_retry(self):
        child = mock.Mock()
        child.wait.side_effect = KeyboardInterrupt
        child.poll.return_value = 1
        with mock.patch("hermachy.executor.subprocess.Popen", return_value=child) as start, mock.patch(
                "hermachy.executor.subprocess.run") as stop:
            self.assertEqual(run_administrator(["/usr/bin/true"], {}), 130)
        start.assert_called_once()
        self.assertEqual(stop.call_args.args[0][:3], ["/usr/bin/systemctl", "--user", "stop"])
        self.assertIn("hermachy-admin-", stop.call_args.args[0][-1])
