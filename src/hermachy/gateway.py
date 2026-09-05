"""Local stdio transport: native Hermes TUI <-> remote agent JSON-RPC."""
from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
from typing import Callable

from .client import ClientError, _config_path, build_ssh_argv, load_client_config
from .protocol import ProtocolError, decode_request

SHUTDOWN_TIMEOUT = 5


def prepare_frame(line: bytes, cwd: str, reasoning: str | None) -> bytes:
    """Supply local workspace and invocation effort on native session requests."""
    frame = json.loads(line)
    if isinstance(frame, dict) and frame.get("method") == "session.create":
        params = frame.setdefault("params", {})
        if not isinstance(params, dict):
            raise ValueError("session.create params must be an object")
        params.setdefault("cwd", cwd)
        if reasoning:
            params.setdefault("reasoning_effort", reasoning)
        return (json.dumps(frame, ensure_ascii=False) + "\n").encode()
    return line


def relay_input(
    child: subprocess.Popen, cwd: str, reasoning: str | None,
    stopping: Callable[[], bool] = lambda: False,
) -> int | None:
    """Forward frames without leaving a buffered-I/O thread alive at exit."""
    input_fd = sys.stdin.fileno()
    output_fd = child.stdin.fileno()
    os.set_blocking(output_fd, False)
    incoming = bytearray()
    outgoing = bytearray()
    eof = False
    while child.poll() is None:
        if stopping():
            return None
        if eof and not outgoing:
            return None
        readable, writable, _ = select.select(
            [input_fd] if not eof and len(outgoing) < 65536 else [],
            [output_fd] if outgoing else [], [], 0.1,
        )
        if readable:
            chunk = os.read(input_fd, 65536)
            incoming.extend(chunk)
            eof = not chunk
            lines = incoming.split(b"\n")
            incoming = lines.pop()
            for line in lines:
                outgoing.extend(prepare_frame(bytes(line) + b"\n", cwd, reasoning))
            if eof and incoming:
                outgoing.extend(prepare_frame(bytes(incoming), cwd, reasoning))
                incoming.clear()
        if writable:
            try:
                written = os.write(output_fd, outgoing)
            except BlockingIOError:
                continue
            del outgoing[:written]
    return child.returncode


def finish_child(child: subprocess.Popen) -> int:
    """Allow EOF cleanup, then bound termination even if SSH is unresponsive."""
    child.stdin.close()
    try:
        return child.wait(timeout=SHUTDOWN_TIMEOUT)
    except subprocess.TimeoutExpired:
        child.terminate()
    try:
        return child.wait(timeout=SHUTDOWN_TIMEOUT)
    except subprocess.TimeoutExpired:
        child.kill()
        return child.wait()


def main() -> int:
    # The upstream GatewayClient treats this executable like Python. Accept
    # exactly its gateway invocation, never arbitrary interpreter arguments.
    if sys.argv[1:] != ["-m", "tui_gateway.entry"]:
        print("hermachy-gateway: expected -m tui_gateway.entry", file=sys.stderr)
        return 2
    try:
        request = decode_request(os.environ.get("HERMACHY_REQUEST", ""))
        if request.action != "gateway":
            raise ProtocolError("expected a gateway request")
        config = load_client_config(_config_path())
        child = subprocess.Popen(build_ssh_argv(config, request), stdin=subprocess.PIPE)
    except (ClientError, ProtocolError, OSError) as exc:
        print(f"hermachy-gateway: {exc}", file=sys.stderr)
        return 2

    requested_signal = 0

    def stop(signum, _frame):
        nonlocal requested_signal
        requested_signal = signum

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}

    result = None
    try:
        try:
            result = relay_input(child, request.cwd, os.environ.get("HERMACHY_REASONING"),
                                 lambda: bool(requested_signal))
        except BrokenPipeError:
            # The child may close its pipe just before its exit becomes visible.
            # Reap it below and preserve its actual status.
            pass
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            print(f"hermachy-gateway: transport input closed: {exc}", file=sys.stderr)
            result = 2
    finally:
        try:
            child_result = finish_child(child)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    if requested_signal:
        return 128 + requested_signal
    code = child_result if result is None else result
    return 128 - code if code < 0 else code
