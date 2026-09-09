"""Keep the TUI's stdio alive while replacing failed SSH attachments."""
from __future__ import annotations

import os
import select
import subprocess
import sys
import time

from .client import build_ssh_argv
from .persistent import write_buffer
from .reliable import CHUNK, WINDOW, ReliableStream

RETRY_MAX = 15
CLOSE_TIMEOUT = 10
MAX_INPUT_FRAME = 16 * 1024 * 1024


def run_connection(config, request, reasoning, stopping, *, retry_base=1):
    from .gateway import finish_child, prepare_frame

    stream = ReliableStream()
    child = None
    next_attempt = 0.0
    failures = 0
    raw, prepared, output = bytearray(), bytearray(), bytearray()
    eof = close_sent = False
    closing_at = None
    remote_exit = None
    os.set_blocking(sys.stdout.fileno(), False)
    try:
        while True:
            now = time.monotonic()
            if stopping():
                eof = True
                raw.clear()
                prepared.clear()
            if eof and closing_at is None:
                closing_at = now
            if closing_at is not None and now - closing_at > CLOSE_TIMEOUT:
                return 1
            if remote_exit is not None and not output and not stream.wire:
                return 128 - remote_exit if remote_exit < 0 else remote_exit
            if child is None and now >= next_attempt:
                child = subprocess.Popen(build_ssh_argv(config, request),
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE)
                os.set_blocking(child.stdin.fileno(), False)
                os.set_blocking(child.stdout.fileno(), False)
                stream.attach()
            if prepared and stream.writable:
                stream.queue(bytes(prepared[:CHUNK]))
                del prepared[:CHUNK]
            if eof and not prepared and not close_sent:
                stream.queue(b"")
                close_sent = True
            readers = []
            if not eof and not prepared and stream.writable:
                readers.append(sys.stdin.fileno())
            if child is not None and len(output) < WINDOW:
                readers.append(child.stdout.fileno())
            writers = [sys.stdout.fileno()] if output else []
            if child is not None and stream.wire:
                writers.append(child.stdin.fileno())
            ready, writable, _ = select.select(readers, writers, [], 0.1)
            disconnected = False
            for fd in ready:
                data = os.read(fd, CHUNK)
                if fd == sys.stdin.fileno():
                    raw.extend(data)
                    eof = not data
                    if len(raw) > MAX_INPUT_FRAME:
                        raise ValueError("TUI input frame exceeds 16 MiB")
                    lines = raw.split(b"\n")
                    raw = lines.pop()
                    if eof and raw:
                        lines.append(raw)
                        raw = bytearray()
                    for line in lines:
                        prepared.extend(prepare_frame(bytes(line) + b"\n", request.cwd, reasoning))
                elif not data:
                    disconnected = True
                else:
                    was_ready = stream.ready
                    for record in stream.feed(data):
                        if isinstance(record, int):
                            remote_exit = record
                        else:
                            output.extend(record)
                    if stream.ready and not was_ready:
                        if failures:
                            print("hermachy: connection restored; continuing the same gateway", file=sys.stderr)
                        failures = 0
            for fd in writable:
                try:
                    write_buffer(fd, output if fd == sys.stdout.fileno() else stream.wire)
                except BrokenPipeError:
                    if fd == sys.stdout.fileno():
                        return 0
                    disconnected = True
            if child is not None and disconnected:
                code = finish_child(child)
                child.stdout.close()
                child = None
                if remote_exit is not None:
                    # The remote exit record is authoritative, even if SSH
                    # closes before accepting our final acknowledgement.
                    stream.wire.clear()
                    continue
                if code not in (0, 255):
                    return 128 - code if code < 0 else code
                failures += 1
                delay = min(retry_base * 2 ** min(failures - 1, 8), RETRY_MAX)
                next_attempt = time.monotonic() + delay
                print(f"hermachy: connection lost; reconnecting in {delay:g}s", file=sys.stderr)
    finally:
        if child is not None:
            finish_child(child)
            child.stdout.close()
