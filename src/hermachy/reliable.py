"""Acknowledged, bounded byte transport for reconnecting SSH streams.

Both peers survive a socket loss. A new stream replays only unacknowledged
records; sequence numbers suppress duplicates before delivery to the consumer.
Losing a peer process is fatal: its sequence state must never be recreated.
"""
from __future__ import annotations

import base64
from collections import deque
import json

WINDOW = 1024 * 1024
CHUNK = 16384
MAX_WIRE_LINE = 32768


def frame(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode() + b"\n"


class ReliableStream:
    def __init__(self):
        self.received = 0
        self.sent = 0
        self.acked = 0
        self.pending = deque()
        self.pending_bytes = 0
        self.incoming = bytearray()
        self.wire = bytearray()
        self.ready = False

    @property
    def writable(self) -> bool:
        return self.pending_bytes < WINDOW and len(self.wire) < WINDOW * 2

    def attach(self):
        self.incoming.clear()
        self.ready = False
        self.wire = bytearray(frame({"hello": 1, "ack": self.received}))

    def queue(self, data: bytes | None = None, *, exit_code: int | None = None):
        self.sent += 1
        record = {"seq": self.sent}
        if exit_code is not None:
            record["exit"] = exit_code
        else:
            record["data"] = base64.b64encode(data or b"").decode()
        encoded = frame(record)
        self.pending.append((self.sent, encoded))
        self.pending_bytes += len(encoded)
        if self.ready:
            self.wire.extend(encoded)

    def _ack(self, ack):
        if type(ack) is not int or not self.acked <= ack <= self.sent:
            raise ValueError("invalid transport acknowledgement")
        self.acked = ack
        while self.pending and self.pending[0][0] <= ack:
            _, encoded = self.pending.popleft()
            self.pending_bytes -= len(encoded)

    def feed(self, chunk: bytes) -> list[bytes | int]:
        self.incoming.extend(chunk)
        result = []
        while b"\n" in self.incoming:
            line, _, remaining = self.incoming.partition(b"\n")
            self.incoming = bytearray(remaining)
            if len(line) > MAX_WIRE_LINE:
                raise ValueError("transport frame too large")
            msg = json.loads(line)
            if not isinstance(msg, dict):
                raise ValueError("transport frame must be an object")
            if not self.ready:
                if set(msg) != {"hello", "ack"} or msg["hello"] != 1:
                    raise ValueError("persistent transport handshake required")
                self._ack(msg["ack"])
                self.ready = True
                for _, encoded in self.pending:
                    self.wire.extend(encoded)
            elif set(msg) == {"ack"}:
                self._ack(msg["ack"])
            elif set(msg) in ({"seq", "data"}, {"seq", "exit"}):
                seq = msg["seq"]
                if type(seq) is not int or seq < 1 or seq > self.received + 1:
                    raise ValueError("transport sequence gap")
                if seq == self.received + 1:
                    if "exit" in msg:
                        data = msg["exit"]
                        if type(data) is not int:
                            raise ValueError("invalid exit status")
                    else:
                        data = base64.b64decode(msg["data"], validate=True)
                        if len(data) > CHUNK:
                            raise ValueError("transport chunk too large")
                    result.append(data)
                    self.received = seq
                self.wire.extend(frame({"ack": self.received}))
            else:
                raise ValueError("invalid transport record")
        if len(self.incoming) > MAX_WIRE_LINE:
            raise ValueError("transport frame too large")
        return result
