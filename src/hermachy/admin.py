"""Idempotent setup and revocation utilities for Hermachy."""

from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import stat
import struct
import subprocess
import sys
import tempfile
from typing import Any, Sequence

from . import __version__

_MARKER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ED25519_TYPE = b"ssh-ed25519"


class AdminError(ValueError):
    """Raised when a setup operation would be unsafe or invalid."""


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    """Atomically replace a user-owned file with exact permissions."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    stream_opened = False
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream_opened = True
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        with contextlib.suppress(OSError):
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except BaseException:
        if not stream_opened:
            with contextlib.suppress(OSError):
                os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
        raise


def install_executable(source: Path, destination: Path) -> None:
    """Install a regular file atomically as mode 0755."""

    if not source.is_file():
        raise AdminError(f"artifact not found: {source}")
    atomic_write(destination, source.read_bytes(), 0o755)


def _marker_lines(marker: str) -> tuple[str, str]:
    if not _MARKER_RE.fullmatch(marker):
        raise AdminError(f"invalid managed-block marker: {marker!r}")
    return (
        f"# >>> hermachy:{marker} >>>",
        f"# <<< hermachy:{marker} <<<",
    )


def _managed_region(text: str, marker: str) -> tuple[int, int] | None:
    begin, end = _marker_lines(marker)
    lines = text.splitlines(keepends=True)
    begin_indices = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == begin]
    end_indices = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == end]
    if not begin_indices and not end_indices:
        return None
    if len(begin_indices) != 1 or len(end_indices) != 1:
        raise AdminError(f"malformed or duplicate managed block: {marker}")
    begin_index, end_index = begin_indices[0], end_indices[0]
    if end_index < begin_index:
        raise AdminError(f"managed block ends before it begins: {marker}")
    start = sum(len(line) for line in lines[:begin_index])
    finish = sum(len(line) for line in lines[: end_index + 1])
    return start, finish


def upsert_marked_block(
    path: Path, marker: str, lines: Sequence[str], mode: int = 0o600
) -> None:
    """Insert or replace one marked text block while preserving all other bytes."""

    begin, end = _marker_lines(marker)
    clean_lines: list[str] = []
    for line in lines:
        if not isinstance(line, str) or "\n" in line or "\r" in line or not line:
            raise AdminError("managed block lines must be non-empty single lines")
        clean_lines.append(line)
    block = "\n".join([begin, *clean_lines, end]) + "\n"

    with _managed_file_lock(path):
        text = _read_managed_text(path)
        region = _managed_region(text, marker)
        if region is None:
            separator = "" if not text or text.endswith(("\n", "\r")) else "\n"
            updated = text + separator + block
        else:
            start, finish = region
            updated = text[:start] + block + text[finish:]
        _write_managed_text_if_unchanged(path, text, updated, mode)


def remove_marked_block(path: Path, marker: str, mode: int = 0o600) -> bool:
    """Remove one managed block, preserving unrelated content exactly."""

    with _managed_file_lock(path):
        text = _read_managed_text(path)
        region = _managed_region(text, marker)
        if region is None:
            return False
        start, finish = region
        updated = text[:start] + text[finish:]
        _write_managed_text_if_unchanged(path, text, updated, mode)
        return True


@contextlib.contextmanager
def _managed_file_lock(path: Path):
    """Serialize Hermachy edits through a stable sidecar lock."""

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".hermachy.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _read_managed_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeDecodeError) as exc:
        raise AdminError(f"cannot read managed file {path}: {exc}") from exc


def _write_managed_text_if_unchanged(
    path: Path, original: str, updated: str, mode: int
) -> None:
    if _read_managed_text(path) != original:
        raise AdminError(f"managed file changed concurrently: {path}")
    atomic_write(path, updated.encode("utf-8"), mode)


def _read_ssh_string(blob: bytes, offset: int) -> tuple[bytes, int]:
    if offset + 4 > len(blob):
        raise AdminError("truncated OpenSSH public key")
    length = struct.unpack(">I", blob[offset : offset + 4])[0]
    offset += 4
    finish = offset + length
    if finish > len(blob):
        raise AdminError("truncated OpenSSH public key")
    return blob[offset:finish], finish


def normalize_ed25519_public_key(text: str, comment: str) -> str:
    """Validate an Ed25519 OpenSSH public key and replace its comment."""

    words = text.strip().split()
    if len(words) < 2 or words[0] != "ssh-ed25519":
        raise AdminError("expected an ssh-ed25519 public key")
    try:
        blob = base64.b64decode(words[1], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AdminError("invalid OpenSSH public-key base64") from exc
    key_type, offset = _read_ssh_string(blob, 0)
    public, offset = _read_ssh_string(blob, offset)
    if key_type != _ED25519_TYPE or len(public) != 32 or offset != len(blob):
        raise AdminError("invalid Ed25519 OpenSSH public-key blob")
    if not comment or any(character.isspace() for character in comment):
        raise AdminError("public-key comment must be one word")
    return f"ssh-ed25519 {words[1]} {comment}"


def _read_public_key(path: Path, comment: str) -> str:
    try:
        return normalize_ed25519_public_key(path.read_text(encoding="utf-8"), comment)
    except FileNotFoundError as exc:
        raise AdminError(f"public key not found: {path}") from exc
    except OSError as exc:
        raise AdminError(f"cannot read public key {path}: {exc}") from exc


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def ensure_ed25519_key(path: Path, comment: str) -> tuple[Path, Path]:
    """Create one dedicated Ed25519 key pair, or validate an existing pair."""

    if not path.is_absolute():
        raise AdminError(f"SSH key path must be absolute: {path}")
    public = path.with_name(path.name + ".pub")
    if path.exists() != public.exists():
        raise AdminError(f"incomplete SSH key pair at {path}")
    _ensure_private_directory(path.parent)
    if not path.exists():
        subprocess.run(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                comment,
                "-f",
                str(path),
            ],
            check=True,
        )
    if not path.is_file() or not public.is_file():
        raise AdminError(f"SSH key pair was not created at {path}")
    path.chmod(0o600)
    public.chmod(0o644)
    stored = _read_public_key(public, comment)
    derived = subprocess.run(
        ["ssh-keygen", "-y", "-f", str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if derived.returncode != 0:
        raise AdminError(f"invalid or unreadable Ed25519 private key: {path}")
    derived_key = normalize_ed25519_public_key(derived.stdout, comment)
    if derived_key.split()[1] != stored.split()[1]:
        raise AdminError(f"SSH private/public key mismatch at {path}")
    return path, public


def _safe_endpoint(value: str, label: str) -> str:
    if not value or any(ord(character) < 33 or ord(character) == 127 for character in value):
        raise AdminError(f"{label} contains whitespace or control characters")
    return value


def _safe_port(value: int, label: str) -> int:
    if type(value) is not int or not 1 <= value <= 65535:
        raise AdminError(f"{label} must be between 1 and 65535")
    return value


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _home_paths() -> tuple[Path, Path, Path]:
    home = Path.home()
    return home / ".ssh", home / ".local" / "bin", home / ".config" / "hermachy"


def _absolute_runtime_path(value: str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise AdminError(f"{label} must resolve to an absolute path")
    return path


def studio_prepare(args: argparse.Namespace) -> int:
    ssh_dir, bin_dir, _ = _home_paths()
    admin_path = None
    if args.admin_artifact:
        admin_path = bin_dir / "hermachy-admin"
        install_executable(Path(args.admin_artifact).expanduser(), admin_path)
    key_path = Path(args.key).expanduser() if args.key else ssh_dir / "hermachy_to_omarchy"
    private, public = ensure_ed25519_key(key_path, "hermachy-studio-executor")
    output_dir = Path(args.output_dir).expanduser()
    _ensure_private_directory(output_dir)
    handoff = output_dir / "studio-executor.pub"
    atomic_write(handoff, public.read_bytes(), 0o644)
    print(
        json.dumps(
            {
                "status": "prepared",
                "private_key": str(private),
                "public_key": str(public),
                "handoff_public_key": str(handoff),
                "admin": str(admin_path) if admin_path else None,
            },
            sort_keys=True,
        )
    )
    return 0


def omarchy_install(args: argparse.Namespace) -> int:
    ssh_dir, default_bin, default_config = _home_paths()
    bin_dir = _absolute_runtime_path(args.bin_dir, "bin_dir") if args.bin_dir else default_bin
    config_dir = default_config
    artifact = Path(args.artifact).expanduser()
    gateway_artifact = artifact.parent / "hermachy-gateway"
    tui_dir = artifact.parent / "tui"
    for required in (artifact, gateway_artifact, tui_dir / "entry.mjs"):
        if not required.is_file():
            raise AdminError(f"incomplete native TUI release: missing {required}")
    if args.admin_artifact and not Path(args.admin_artifact).expanduser().is_file():
        raise AdminError(f"artifact not found: {args.admin_artifact}")
    host = _safe_endpoint(args.studio_host, "studio_host")
    user = _safe_endpoint(args.studio_user, "studio_user")
    port = _safe_port(args.studio_port, "studio_port")
    executor_key = _read_public_key(
        Path(args.studio_executor_public_key).expanduser(),
        "hermachy-studio-executor",
    )
    _ensure_private_directory(ssh_dir)
    _ensure_private_directory(config_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    key_path = (
        _absolute_runtime_path(args.client_key, "client_key")
        if args.client_key
        else ssh_dir / "hermachy_to_morpheus"
    )
    private, public = ensure_ed25519_key(key_path, "hermachy-omarchy-client")
    client_path = bin_dir / "hermachy"
    install_executable(artifact, client_path)
    # Native TUI assets travel with the zipapps in a release directory.
    install_executable(gateway_artifact, bin_dir / "hermachy-gateway")
    target_tui = Path.home() / ".local" / "share" / "hermachy" / "tui"
    target_tui.mkdir(parents=True, exist_ok=True)
    for source in tui_dir.iterdir():
        if source.is_file():
            atomic_write(target_tui / source.name, source.read_bytes(), 0o644)

    admin_path = None
    if args.admin_artifact:
        admin_path = bin_dir / "hermachy-admin"
        install_executable(Path(args.admin_artifact).expanduser(), admin_path)

    upsert_marked_block(
        ssh_dir / "authorized_keys",
        "studio-executor",
        [f"restrict {executor_key}"],
        0o600,
    )

    config = {
        "identity_file": str(private),
        "studio_host": host,
        "studio_port": port,
        "studio_user": user,
    }
    config_path = config_dir / "client.json"
    atomic_write(config_path, _json_bytes(config), 0o600)
    print(
        json.dumps(
            {
                "status": "installed",
                "client": str(client_path),
                "config": str(config_path),
                "client_public_key": str(public),
                "admin": str(admin_path) if admin_path else None,
            },
            sort_keys=True,
        )
    )
    return 0


def _quote_authorized_command(path: Path) -> str:
    text = str(path)
    if "\n" in text or "\r" in text or "\x00" in text:
        raise AdminError("server executable path contains control characters")
    shell_word = shlex.quote(text)
    return shell_word.replace("\\", "\\\\").replace('"', '\\"')


def studio_finish(args: argparse.Namespace) -> int:
    ssh_dir, default_bin, default_config = _home_paths()
    bin_dir = _absolute_runtime_path(args.bin_dir, "bin_dir") if args.bin_dir else default_bin
    config_dir = default_config
    _ensure_private_directory(ssh_dir)
    _ensure_private_directory(config_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)

    artifact = Path(args.artifact).expanduser()
    server_path = bin_dir / "hermachy-server"
    if not artifact.is_file():
        raise AdminError(f"artifact not found: {artifact}")
    client_key = _read_public_key(
        Path(args.client_public_key).expanduser(), "hermachy-omarchy-client"
    )

    executor_key = (
        _absolute_runtime_path(args.executor_key, "executor_key")
        if args.executor_key
        else ssh_dir / "hermachy_to_omarchy"
    )
    executor_public = executor_key.with_name(executor_key.name + ".pub")
    if not executor_key.is_file() or not executor_public.is_file():
        raise AdminError("Studio execution key is missing; run studio-prepare first")
    executor_key, executor_public = ensure_ed25519_key(
        executor_key, "hermachy-studio-executor"
    )

    hermes_source_root = Path(args.hermes_source_root).expanduser()
    if not hermes_source_root.is_absolute() or not hermes_source_root.is_dir():
        raise AdminError(f"Hermes source root is missing: {hermes_source_root}")
    runtime_bin = hermes_source_root / "venv" / "bin"
    hermes_command = runtime_bin / "hermes"
    runtime_python = runtime_bin / "python"
    for runtime_path, label in (
        (hermes_command, "Hermes runtime command"),
        (runtime_python, "Hermes runtime Python"),
    ):
        if not runtime_path.is_file() or not os.access(runtime_path, os.X_OK):
            raise AdminError(f"{label} is missing or not executable: {runtime_path}")
    if args.hermes_command:
        requested_command = Path(args.hermes_command).expanduser()
        if (
            not requested_command.is_absolute()
            or not requested_command.is_file()
            or not os.access(requested_command, os.X_OK)
        ):
            raise AdminError(
                f"Hermes command is missing or not executable: {requested_command}"
            )
        try:
            same_runtime = os.path.samefile(requested_command, hermes_command)
        except OSError as exc:
            raise AdminError(f"cannot verify Hermes runtime command: {exc}") from exc
        if not same_runtime:
            raise AdminError(
                "--hermes-command must identify the same Hermes runtime as "
                "--hermes-source-root/venv/bin/hermes"
            )

    host = _safe_endpoint(args.executor_host, "executor_host")
    user = _safe_endpoint(args.executor_user, "executor_user")
    port = _safe_port(args.executor_port, "executor_port")
    config = {
        "executor_host": host,
        "executor_identity_file": str(executor_key),
        "executor_port": port,
        "executor_user": user,
        "hermes_command": str(hermes_command),
        "hermes_source_root": str(hermes_source_root),
    }
    config_path = config_dir / "server.json"
    install_executable(artifact, server_path)
    atomic_write(config_path, _json_bytes(config), 0o600)

    forced = _quote_authorized_command(server_path)
    upsert_marked_block(
        ssh_dir / "authorized_keys",
        "omarchy-client",
        [f'restrict,pty,command="{forced}" {client_key}'],
        0o600,
    )
    print(
        json.dumps(
            {
                "status": "installed",
                "server": str(server_path),
                "config": str(config_path),
                "executor": f"{user}@{host}:{port}",
            },
            sort_keys=True,
        )
    )
    return 0


def revoke(args: argparse.Namespace) -> int:
    ssh_dir, _, _ = _home_paths()
    marker = "omarchy-client" if args.side == "studio" else "studio-executor"
    removed = remove_marked_block(ssh_dir / "authorized_keys", marker, 0o600)
    print(json.dumps({"status": "revoked" if removed else "not-found", "side": args.side}))
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="hermachy-admin", description="Install and revoke Hermachy endpoints"
    )
    result.add_argument("--version", action="version", version=f"Hermachy admin {__version__}")
    commands = result.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("studio-prepare", help="create the Studio execution key")
    prepare.add_argument("--output-dir", required=True)
    prepare.add_argument("--key")
    prepare.add_argument("--admin-artifact")
    prepare.set_defaults(handler=studio_prepare)

    omarchy = commands.add_parser("omarchy-install", help="install the Omarchy client")
    omarchy.add_argument("--artifact", required=True)
    omarchy.add_argument("--studio-host", required=True)
    omarchy.add_argument("--studio-user", required=True)
    omarchy.add_argument("--studio-port", type=int, default=22)
    omarchy.add_argument("--studio-executor-public-key", required=True)
    omarchy.add_argument("--admin-artifact")
    omarchy.add_argument("--client-key")
    omarchy.add_argument("--bin-dir")
    omarchy.set_defaults(handler=omarchy_install)

    finish = commands.add_parser("studio-finish", help="install and authorize the Studio server")
    finish.add_argument("--artifact", required=True)
    finish.add_argument("--executor-host", required=True)
    finish.add_argument("--executor-user", required=True)
    finish.add_argument("--executor-port", type=int, default=22)
    finish.add_argument("--client-public-key", required=True)
    finish.add_argument("--executor-key")
    finish.add_argument(
        "--hermes-command",
        help="optional compatibility check; must be the source root's venv/bin/hermes",
    )
    finish.add_argument("--hermes-source-root", default="~/.hermes/hermes-agent")
    finish.add_argument("--bin-dir")
    finish.set_defaults(handler=studio_finish)

    revoke_parser = commands.add_parser("revoke", help="remove the relevant authorized key")
    revoke_parser.add_argument("--side", choices=("studio", "omarchy"), required=True)
    revoke_parser.set_defaults(handler=revoke)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parser().parse_args(argv)
        return int(args.handler(args))
    except (AdminError, OSError, subprocess.SubprocessError) as exc:
        print(f"hermachy-admin: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
