import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from hermachy.reliable import CHUNK, ReliableStream, frame

ROOT = str(Path(__file__).resolve().parents[1] / "src")


def transfer(source, target):
    data = bytes(source.wire)
    source.wire.clear()
    return target.feed(data)


class ReliableTests(unittest.TestCase):
    def test_lost_ack_replay_delivers_input_once(self):
        client, server = ReliableStream(), ReliableStream()
        client.attach()
        server.attach()
        transfer(client, server)
        transfer(server, client)
        client.queue(b"run command\n")
        self.assertEqual(transfer(client, server), [b"run command\n"])
        # Drop the acknowledgement, then reconnect both surviving peers.
        client.attach()
        server.attach()
        transfer(client, server)
        self.assertEqual(transfer(server, client), [])
        self.assertEqual(transfer(client, server), [])
        self.assertFalse(client.pending)

    def test_partial_output_is_replayed_without_corruption(self):
        client, server = ReliableStream(), ReliableStream()
        client.attach()
        server.attach()
        transfer(client, server)
        transfer(server, client)
        server.queue(b"\xff" * CHUNK)
        self.assertEqual(client.feed(bytes(server.wire[:50])), [])
        client.attach()
        server.attach()
        transfer(client, server)
        self.assertEqual(transfer(server, client), [b"\xff" * CHUNK])

    def test_protocol_rejects_sequence_gaps_and_recreated_peer(self):
        receiver = ReliableStream()
        receiver.attach()
        receiver.feed(frame({"hello": 1, "ack": 0}))
        with self.assertRaises(ValueError):
            receiver.feed(frame({"seq": 2, "data": "YWJj"}))
        sender = ReliableStream()
        sender.attach()
        with self.assertRaises(ValueError):
            sender.feed(frame({"hello": 1, "ack": 10}))


class SupervisorTests(unittest.TestCase):
    def start_worker(self, command, detach_timeout=5):
        directory = tempfile.TemporaryDirectory(prefix="hm-")
        self.addCleanup(directory.cleanup)
        path = str(Path(directory.name) / "s")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(path)
        listener.listen(1)
        script = (
            "import socket,sys;sys.path.insert(0,sys.argv[1]);"
            "from hermachy.persistent import supervise;"
            "s=socket.socket(fileno=int(sys.argv[2]));"
            "raise SystemExit(supervise(s,sys.argv[4:],detach_timeout=float(sys.argv[3])))"
        )
        worker = subprocess.Popen([sys.executable, "-c", script, ROOT,
                                   str(listener.fileno()), str(detach_timeout),
                                   sys.executable, "-u", "-c", command],
                                  pass_fds=(listener.fileno(),), stderr=subprocess.PIPE)
        listener.close()

        def cleanup():
            if worker.poll() is None:
                worker.terminate()
            worker.communicate(timeout=5)
        self.addCleanup(cleanup)
        return path, worker

    def connect(self, path, stream):
        peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        peer.connect(path)
        peer.settimeout(4)
        self.addCleanup(peer.close)
        stream.attach()
        return peer

    def pump_until(self, peer, stream, predicate):
        result = []
        deadline = time.monotonic() + 5
        while not predicate(result):
            if stream.wire:
                peer.sendall(stream.wire)
                stream.wire.clear()
            if time.monotonic() > deadline:
                self.fail("gateway transport timed out")
            if select.select([peer], [], [], 0.05)[0]:
                data = peer.recv(CHUNK)
                if not data:
                    self.fail("gateway closed before expected output")
                result.extend(stream.feed(data))
        if stream.wire:
            peer.sendall(stream.wire)
            stream.wire.clear()
        return result

    def test_command_survives_drop_and_runs_once(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = str(Path(directory) / "count")
            command = (
                "import sys,time,pathlib;print('ready',flush=True);"
                "line=sys.stdin.readline();"
                f"pathlib.Path({marker!r}).write_text(line);"
                "time.sleep(.3);print('completed:'+line,flush=True);"
                "sys.stdin.read()"
            )
            path, worker = self.start_worker(command)
            stream = ReliableStream()
            peer = self.connect(path, stream)
            self.pump_until(peer, stream, lambda records: b"ready\n" in records)
            stream.queue(b"only once\n")
            peer.sendall(stream.wire)
            stream.wire.clear()
            peer.close()  # Lose the command receipt and its future output.
            time.sleep(.5)
            peer = self.connect(path, stream)
            records = self.pump_until(peer, stream, lambda r: any(
                isinstance(x, bytes) and b"completed:" in x for x in r))
            self.assertEqual(b"".join(records), b"completed:only once\n\n")
            self.assertEqual(Path(marker).read_text(), "only once\n")
            stream.queue(b"")
            self.pump_until(peer, stream, lambda r: 0 in r)
            self.assertEqual(worker.wait(timeout=4), 0)

    def test_abandoned_gateway_expires_and_reaps_child(self):
        path, worker = self.start_worker("import time;time.sleep(60)", detach_timeout=.2)
        self.assertEqual(worker.wait(timeout=5), 1)

    def test_large_output_survives_reconnect_and_backpressure(self):
        size = 2 * 1024 * 1024
        path, worker = self.start_worker(
            f"import sys;sys.stdin.readline();sys.stdout.buffer.write(b'x'*{size});"
            "sys.stdout.flush();sys.stdin.read()"
        )
        stream = ReliableStream()
        peer = self.connect(path, stream)
        stream.queue(b"start\n")
        self.pump_until(peer, stream, lambda r: stream.acked == 1)
        peer.close()
        time.sleep(.2)
        peer = self.connect(path, stream)
        collected = bytearray()

        def complete(records):
            collected.extend(b"".join(r for r in records if isinstance(r, bytes)))
            records.clear()
            return len(collected) == size

        self.pump_until(peer, stream, complete)
        self.assertEqual(collected, b"x" * size)
        stream.queue(b"")
        self.pump_until(peer, stream, lambda r: 0 in r)
        self.assertEqual(worker.wait(timeout=4), 0)


class ConnectionTests(unittest.TestCase):
    def test_full_client_reconnect_during_command(self):
        with tempfile.TemporaryDirectory(prefix="hm-") as directory:
            root = Path(directory)
            count = root / "count"
            path = root / "s.sock"
            first = root / "first"
            command = (
                "import sys,time,pathlib,json\n"
                "for line in sys.stdin:\n"
                f" with open({str(count)!r},'a') as f: f.write('executed\\n')\n"
                " time.sleep(.5)\n"
                " print(line,end='',flush=True)\n"
            )
            shim = root / "ssh.py"
            shim.write_text(
                "import os,pathlib,sys,threading,time\n"
                f"sys.path.insert(0,{ROOT!r})\n"
                "from hermachy import persistent\n"
                f"persistent.socket_path=lambda _:pathlib.Path({str(path)!r})\n"
                f"persistent.marker_path=lambda _:pathlib.Path({str(path.with_suffix('.started'))!r})\n"
                f"first=pathlib.Path({str(first)!r})\n"
                "if not first.exists():\n"
                " first.touch()\n"
                " def drop():\n"
                "  time.sleep(.3)\n"
                "  os._exit(255)\n"
                " threading.Thread(target=drop,daemon=True).start()\n"
                f"raise SystemExit(persistent.attach_gateway('id',[sys.executable,'-u','-c',{command!r}],dict(os.environ)))\n"
            )
            driver = (
                "import sys;from types import SimpleNamespace;"
                f"sys.path.insert(0,{ROOT!r});"
                "from hermachy import connection;"
                f"connection.build_ssh_argv=lambda *_:[sys.executable,{str(shim)!r}];"
                "raise SystemExit(connection.run_connection(None,"
                "SimpleNamespace(cwd='/workspace'),None,lambda:False,retry_base=.01))"
            )
            proc = subprocess.Popen([sys.executable, "-c", driver], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                data = b'{"method":"session.create","params":{}}\n'
                out, err = proc.communicate(data, timeout=8)
                self.assertEqual(proc.returncode, 0, err.decode())
                self.assertIn(b"connection restored", err)
                self.assertEqual(json.loads(out)["params"]["cwd"], "/workspace")
                self.assertEqual(count.read_text(), "executed\n")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()
                marker = path.with_suffix(".started")
                if marker.exists() and marker.read_text().strip():
                    try:
                        os.kill(int(marker.read_text()), signal.SIGTERM)
                    except ProcessLookupError:
                        pass
