"""Workstation entry point for the dedicated Hermes execution key.

SSH authenticates the normal workstation user. This entry point supplies that
user's current desktop environment without copying the remote Hermes profile.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import uuid

from . import __version__

EXECUTOR_VERSION = "1"
# Only desktop connection settings cross from the local user manager. In
# particular, never import provider credentials, shell hooks, or Python paths.
DESKTOP_ENV = frozenset({
    "DISPLAY", "WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "XAUTHORITY",
    "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XDG_SESSION_TYPE",
    "XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION",
    "XDG_CONFIG_DIRS", "XDG_DATA_DIRS", "OMARCHY_PATH",
    "QT_QPA_PLATFORM", "QT_QPA_PLATFORMTHEME", "GDK_BACKEND",
    "ELECTRON_OZONE_PLATFORM_HINT", "MOZ_ENABLE_WAYLAND",
    "PULSE_SERVER", "PIPEWIRE_REMOTE",
})


def _owned_socket(path: Path) -> bool:
    try:
        info = path.stat()
        return info.st_uid == os.getuid() and stat.S_ISSOCK(info.st_mode)
    except OSError:
        return False


def _manager_environment(env: dict[str, str]) -> dict[str, str]:
    try:
        result = subprocess.run(
            ["/usr/bin/systemctl", "--user", "show-environment", "--output=json"],
            env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=3, check=False,
        )
        values = json.loads(result.stdout) if result.returncode == 0 else {}
        if isinstance(values, dict):
            return {key: value for key, value in values.items()
                    if key in DESKTOP_ENV | {"PATH"} and isinstance(value, str)
                    and "\x00" not in value}
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return {}


def desktop_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    for key in DESKTOP_ENV:
        env.pop(key, None)
    runtime = Path(f"/run/user/{os.getuid()}")
    env["XDG_RUNTIME_DIR"] = str(runtime)
    env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime}/bus"
    manager = _manager_environment(env)
    env.update({key: value for key, value in manager.items() if key in DESKTOP_ENV})
    # The SSH login's PATH often omits both user-installed and Omarchy commands.
    # Keep only absolute entries; never implicitly search the working directory.
    paths = [str(Path.home() / ".local/bin"), "/usr/share/omarchy/bin",
             str(Path.home() / ".local/share/mise/shims")]
    paths += manager.get("PATH", "").split(":") + env.get("PATH", "").split(":")
    paths += ["/usr/local/sbin", "/usr/local/bin", "/usr/bin"]
    env["PATH"] = ":".join(dict.fromkeys(p for p in paths if p.startswith("/")))

    signature = env.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    if not signature or "/" in signature or not _owned_socket(runtime / "hypr" / signature / ".socket.sock"):
        env.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
        # UWSM normally publishes the session into systemd. Recover if it has
        # not yet done so or the compositor was restarted. Never guess between
        # multiple live desktops.
        try:
            result = subprocess.run(
                ["/usr/bin/hyprctl", "-j", "instances"], env=env,
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=3, check=False,
            )
            instances = json.loads(result.stdout) if result.returncode == 0 else []
            if not isinstance(instances, list):
                instances = []
            live = [item for item in instances if isinstance(item, dict)
                    and isinstance(item.get("instance"), str)
                    and "/" not in item["instance"]
                    and _owned_socket(runtime / "hypr" / item["instance"] / ".socket.sock")]
            if len(live) == 1:
                env["HYPRLAND_INSTANCE_SIGNATURE"] = live[0]["instance"]
                display = live[0].get("wl_socket")
                if isinstance(display, str):
                    env["WAYLAND_DISPLAY"] = display
                env.update(XDG_CURRENT_DESKTOP="Hyprland", XDG_SESSION_TYPE="wayland")
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            pass
    display = env.get("WAYLAND_DISPLAY")
    if display and not _owned_socket(runtime / display):
        env.pop("WAYLAND_DISPLAY", None)
    env["HERMACHY_EXECUTOR_VERSION"] = EXECUTOR_VERSION
    env.pop("SSH_ORIGINAL_COMMAND", None)
    return env


def diagnostics(env: dict[str, str]) -> dict:
    desktop = False
    if env.get("HYPRLAND_INSTANCE_SIGNATURE"):
        try:
            result = subprocess.run(
                ["/usr/bin/hyprctl", "-j", "monitors"], env=env,
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=3, check=False,
            )
            desktop = result.returncode == 0 and isinstance(json.loads(result.stdout), list)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    commands = {name: shutil.which(name, path=env["PATH"])
                for name in ("omarchy", "hyprctl", "hermachy-exec", "pkexec")}
    return {
        "version": EXECUTOR_VERSION,
        "ssh_entrypoint": os.environ.get("HERMACHY_EXECUTOR_VERSION") == EXECUTOR_VERSION,
        "desktop": desktop,
        "wayland": bool(env.get("WAYLAND_DISPLAY")),
        "user_bus": _owned_socket(Path(env["XDG_RUNTIME_DIR"]) / "bus"),
        "commands": commands,
        "administration": {"command": "hermachy-exec --admin COMMAND [ARG ...]",
                           "authentication": "desktop",
                           "available": bool(commands["pkexec"]) and desktop},
    }


def administrator_argv(command: list[str], unit: str) -> list[str]:
    # A pkexec process in the SSH login belongs to the SSH session, which has
    # no graphical authentication agent. A user service belongs to the local
    # user manager, allowing polkit to find that user's graphical session.
    return ["/usr/bin/systemd-run", "--user", "--quiet", "--wait", "--pipe",
            "--collect", "--service-type=exec", f"--unit={unit}",
            f"--working-directory={os.getcwd()}", "--property=TimeoutStopSec=5s",
            "--", "/usr/bin/pkexec", "--disable-internal-agent", "--keep-cwd", *command]


def run_administrator(command: list[str], env: dict[str, str]) -> int:
    unit = f"hermachy-admin-{uuid.uuid4().hex}.service"
    caught = 0

    def interrupted(signum, _frame):
        nonlocal caught
        caught = signum
        raise KeyboardInterrupt

    previous = {sig: signal.signal(sig, interrupted)
                for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    child = None
    try:
        child = subprocess.Popen(administrator_argv(command, unit), env=env)
        code = child.wait()
        return 128 - code if code < 0 else code
    except KeyboardInterrupt:
        return 128 + (caught or signal.SIGINT)
    finally:
        # Stop pending authentication as well as its command when the caller
        # disconnects or cancels. Never leave a delayed privileged operation.
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        try:
            subprocess.run(["/usr/bin/systemctl", "--user", "stop", unit], env=env,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=8, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    original = os.environ.get("SSH_ORIGINAL_COMMAND")
    try:
        if original is not None:
            if args or not original.strip() or "\x00" in original:
                raise ValueError("a non-empty SSH command is required")
            env = desktop_environment()
            # Preserve byte streams and exit status. The authenticated key
            # already grants arbitrary user commands; this is no shell filter.
            os.execve("/bin/bash", ["bash", "--noprofile", "--norc", "-c", original], env)
            return 126
        parser = argparse.ArgumentParser(description="Hermachy workstation execution and desktop access")
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--version", action="store_true")
        group.add_argument("--doctor", action="store_true")
        group.add_argument("--admin", nargs=argparse.REMAINDER, metavar="COMMAND")
        parsed = parser.parse_args(args)
        if parsed.version:
            print(f"Hermachy executor {__version__} (desktop access v{EXECUTOR_VERSION})")
            return 0
        env = desktop_environment()
        if parsed.doctor:
            print(json.dumps(diagnostics(env), sort_keys=True))
            return 0
        if not parsed.admin:
            raise ValueError("--admin requires a command and optional arguments")
        command = shutil.which(parsed.admin[0], path=env["PATH"])
        if command is None:
            raise ValueError(f"command not found: {parsed.admin[0]}")
        # No terminal password reading over the agent's tool stream. Polkit
        # performs authentication using the workstation's graphical agent.
        return run_administrator([command, *parsed.admin[1:]], env)
    except (OSError, ValueError) as exc:
        print(f"hermachy-exec: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
