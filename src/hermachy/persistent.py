"""Private remote gateway supervisor, reachable only through forced SSH.

The gateway's pipes belong to this detached supervisor, so loss of an SSH
attachment does not send EOF to Hermes. Unacknowledged bytes are replayed on
reattachment. A dead supervisor is never silently replaced for the same ID.
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
import select
import signal
import socket
import stat
import subprocess
import sys
import time

from .reliable import CHUNK, WINDOW, ReliableStream

DETACH_TIMEOUT = 3600
STOP_TIMEOUT = 10


def module_command(python: str, module: str, *args: str) -> list[str]:
    # Works from the source tree and from the installed server zipapp.
    package_root = str(Path(__file__).resolve().parent.parent)
    return [python, "-c", "import sys,runpy;sys.path.insert(0,sys.argv.pop(1));"
            "runpy.run_module(sys.argv.pop(1),run_name='__main__')",
            package_root, module, *args]


def socket_path(connection: str) -> Path:
    # macOS AF_UNIX paths are limited to 104 bytes; HOME may already exceed it.
    root = Path(f"/tmp/hermachy-{os.getuid()}")
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("persistent gateway directory must be private and owned by this user")
    return root / (connection + ".sock")


def marker_path(connection: str) -> Path:
    # Persist across remote reboots, including ones that clear /tmp. Otherwise
    # an unacknowledged command could be replayed into a fresh gateway.
    root = Path("~/.local/state/hermachy/connections").expanduser()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("gateway connection state must be private and owned by this user")
    return root / (connection + ".started")


def attach_gateway(connection: str, command: list[str], env: dict[str, str]) -> int:
    path = socket_path(connection)
    # O_EXCL is both a creation lock and a tombstone after termination. Reusing
    # the ID with fresh sequence state could execute a submitted command twice.
    marker = marker_path(connection)
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(fd)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(path))
            os.chmod(path, 0o600)
            listener.listen(1)
            worker = module_command(command[0], "hermachy.persistent", str(listener.fileno()), *command)
            log_path = path.with_suffix(".log")
            log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(log_fd, "wb") as log:
                proc = subprocess.Popen(worker, env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=log,
                                        pass_fds=(listener.fileno(),), start_new_session=True)
            marker.write_text(str(proc.pid) + "\n")
        finally:
            listener.close()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
        deadline = time.monotonic() + 10
        while True:
            try:
                peer.connect(str(path))
                break
            except (ConnectionRefusedError, FileNotFoundError):
                if time.monotonic() >= deadline:
                    raise ValueError("persistent gateway ended; resume the session in a new connection")
                time.sleep(0.1)
        return relay_socket(peer)


def relay_socket(peer: socket.socket) -> int:
    """Bounded nonblocking stdio/socket proxy; EOF detaches this attachment."""
    peer.setblocking(False)
    os.set_blocking(1, False)
    to_peer, to_stdout = bytearray(), bytearray()
    while True:
        readers = ([0] if len(to_peer) < WINDOW else [])
        if len(to_stdout) < WINDOW:
            readers.append(peer)
        writers = ([peer] if to_peer else []) + ([1] if to_stdout else [])
        ready, writable, _ = select.select(readers, writers, [], 0.2)
        for item in ready:
            data = os.read(0, CHUNK) if item == 0 else peer.recv(CHUNK)
            if not data:
                # Flush remaining output before reporting worker EOF.
                if item != 0:
                    while to_stdout:
                        if select.select([], [1], [], 1)[1]:
                            write_buffer(1, to_stdout)
                return 0
            (to_peer if item == 0 else to_stdout).extend(data)
        for item in writable:
            write_buffer(peer.fileno() if item == peer else 1,
                         to_peer if item == peer else to_stdout)


def write_buffer(fd: int, buffer: bytearray):
    try:
        written = os.write(fd, buffer)
    except BlockingIOError:
        return
    del buffer[:written]


def supervise(listener: socket.socket, command: list[str], *, detach_timeout=DETACH_TIMEOUT) -> int:
    stream = ReliableStream()
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             start_new_session=True)
    for pipe in (child.stdin, child.stdout):
        os.set_blocking(pipe.fileno(), False)
    listener.setblocking(False)
    peer = None
    incoming = bytearray()
    input_eof = output_eof = exit_sent = False
    detached_at = time.monotonic()
    closing_at = None
    stopped = False

    def stop(_sig, _frame):
        nonlocal stopped
        stopped = True

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        while not stopped:
            now = time.monotonic()
            if peer is None and now - detached_at > detach_timeout:
                break
            if closing_at is not None and now - closing_at > STOP_TIMEOUT:
                break
            if input_eof and not incoming and not child.stdin.closed:
                child.stdin.close()
                closing_at = now
            if output_eof and child.poll() is not None and not exit_sent:
                stream.queue(exit_code=child.returncode)
                exit_sent = True
                closing_at = now
            if exit_sent and not stream.pending and not stream.wire:
                return child.returncode
            readers = [listener]
            if not output_eof and stream.writable:
                readers.append(child.stdout)
            if peer is not None and len(incoming) < WINDOW and len(stream.wire) < WINDOW * 2:
                readers.append(peer)
            writers = ([child.stdin] if incoming and not child.stdin.closed else [])
            if peer is not None and stream.wire:
                writers.append(peer)
            ready, writable, _ = select.select(readers, writers, [], 0.1)
            if listener in ready:
                replacement, _ = listener.accept()
                replacement.setblocking(False)
                if peer is not None:
                    peer.close()
                peer = replacement
                stream.attach()
                # Readiness lists still refer to the old attachment.
                continue
            for item in ready:
                if item == child.stdout:
                    data = os.read(child.stdout.fileno(), CHUNK)
                    if data:
                        stream.queue(data)
                    else:
                        output_eof = True
                elif item == peer:
                    try:
                        data = peer.recv(CHUNK)
                        if not data:
                            raise ConnectionResetError()
                        for record in stream.feed(data):
                            if not isinstance(record, bytes) or (input_eof and record):
                                raise ValueError("invalid gateway input after EOF")
                            if not record:
                                input_eof = True
                            else:
                                incoming.extend(record)
                    except (OSError, ValueError, TypeError):
                        peer.close()
                        peer = None
                        detached_at = now
            for item in writable:
                try:
                    if item == child.stdin:
                        write_buffer(child.stdin.fileno(), incoming)
                    elif item == peer:
                        write_buffer(peer.fileno(), stream.wire)
                except OSError:
                    if item == child.stdin:
                        incoming.clear()
                        child.stdin.close()
                    elif peer is not None:
                        peer.close()
                        peer = None
                        detached_at = now
        return 0 if child.poll() == 0 else 1
    finally:
        if peer is not None:
            peer.close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        # Bound teardown of the entire gateway process group, including tools.
        if child.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGKILL)
        child.wait()
        child.stdin.close()
        child.stdout.close()


if __name__ == "__main__":
    with socket.socket(fileno=int(sys.argv[1])) as listener:
        address = listener.getsockname()
        try:
            raise SystemExit(supervise(listener, sys.argv[2:]))
        finally:
            with contextlib.suppress(FileNotFoundError):
                Path(address).unlink()
