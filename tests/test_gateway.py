import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parent
ARCHIVE = os.environ.get('GATEWAY_UNDER_TEST', str(ROOT.parent / 'src'))
DRIVER = '''
import sys
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from hermes_link import gateway
child_script = sys.argv[2]
gateway.SHUTDOWN_TIMEOUT = 0.2
gateway.decode_request = lambda _: SimpleNamespace(action='gateway', cwd='/workspace')
gateway.load_client_config = lambda _: None
gateway.build_ssh_argv = lambda *_: [sys.executable, '-I', '-c', child_script]
sys.argv = ['hlink-gateway', '-m', 'tui_gateway.entry']
raise SystemExit(gateway.main())
'''


def disable_core():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


class GatewayTests(unittest.TestCase):
    def spawn(self, script):
        env = dict(os.environ, HLINK_REASONING='high')
        proc = subprocess.Popen(
            [sys.executable, '-I', '-c', DRIVER, ARCHIVE, script],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, preexec_fn=disable_core, start_new_session=True,
        )
        def cleanup():
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()
        self.addCleanup(cleanup)
        return proc

    def test_child_exit_with_input_open(self):
        proc = self.spawn('import time; time.sleep(0.25); raise SystemExit(7)')
        # Keep stdin open: this leaves the old daemon blocked in BufferedReader.
        code = proc.wait(timeout=4)
        err = proc.stderr.read()
        self.assertEqual(code, 7, err.decode())
        self.assertNotIn(b'Fatal Python error', err)

    def test_child_exit_with_partial_frame(self):
        proc = self.spawn('import time; time.sleep(0.25); raise SystemExit(9)')
        proc.stdin.write(b'{"method":')
        proc.stdin.flush()
        self.assertEqual(proc.wait(timeout=4), 9, proc.stderr.read().decode())

    def test_repeated_disconnects_do_not_abort(self):
        for attempt in range(20):
            with self.subTest(attempt=attempt):
                proc = self.spawn('raise SystemExit(7)')
                self.assertEqual(proc.wait(timeout=4), 7, proc.stderr.read().decode())

    def test_forward_frames_and_eof(self):
        proc = self.spawn('import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())')
        frames = [
            {'method': 'session.create', 'params': {}},
            {'method': 'session.create', 'params': {'cwd': '/explicit', 'reasoning_effort': 'low'}},
            {'method': 'ping'},
        ]
        # Deliberately leave the final frame without a trailing newline.
        data = b'\n'.join(json.dumps(frame).encode() for frame in frames)
        out, err = proc.communicate(data, timeout=4)
        self.assertEqual(proc.returncode, 0, err.decode())
        actual = [json.loads(line) for line in out.splitlines()]
        frames[0]['params'] = {'cwd': '/workspace', 'reasoning_effort': 'high'}
        self.assertEqual(actual, frames)

    def test_backpressure_does_not_block_exit(self):
        proc = self.spawn('import time; time.sleep(0.4); raise SystemExit(11)')
        data = (json.dumps({'method': 'ping', 'padding': 'x' * 4096}) + '\n').encode() * 128
        out, err = proc.communicate(data, timeout=4)
        self.assertEqual(proc.returncode, 11, err.decode())

    def test_large_frames_forward_without_truncation(self):
        proc = self.spawn('import time, sys; time.sleep(0.2); sys.stdout.buffer.write(sys.stdin.buffer.read())')
        data = (json.dumps({'method': 'ping', 'padding': 'x' * 100000}) + '\n').encode() * 4
        out, err = proc.communicate(data, timeout=4)
        self.assertEqual(proc.returncode, 0, err.decode())
        self.assertEqual(out, data)

    def test_signal_while_input_open(self):
        proc = self.spawn('import time; print("ready", flush=True); time.sleep(30)')
        self.assertEqual(proc.stdout.readline(), b'ready\n')
        proc.send_signal(signal.SIGTERM)
        code = proc.wait(timeout=4)
        err = proc.stderr.read()
        self.assertNotEqual(code, -signal.SIGABRT, err.decode())
        self.assertNotIn(b'Fatal Python error', err)

    def test_invalid_input_closes_transport(self):
        proc = self.spawn('import sys; sys.stdin.buffer.read()')
        out, err = proc.communicate(b'not json\n', timeout=4)
        self.assertNotEqual(proc.returncode, 0, err.decode())
        self.assertIn(b'transport input closed', err)

    def test_eof_reaps_child_that_ignores_input_and_termination(self):
        proc = self.spawn('import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print("ready", flush=True); time.sleep(30)')
        self.assertEqual(proc.stdout.readline(), b'ready\n')
        out, err = proc.communicate(b'', timeout=3)
        self.assertNotEqual(proc.returncode, 0, err.decode())

    def test_signal_reaps_child_that_ignores_termination(self):
        proc = self.spawn('import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print("ready", flush=True); time.sleep(30)')
        self.assertEqual(proc.stdout.readline(), b'ready\n')
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=3), 128 + signal.SIGTERM)

    def test_bad_session_params_fail_cleanly(self):
        for params in [None, [], 'bad']:
            with self.subTest(params=params):
                proc = self.spawn('import sys; sys.stdin.buffer.read()')
                out, err = proc.communicate(json.dumps({'method': 'session.create', 'params': params}).encode() + b'\n', timeout=3)
                self.assertNotEqual(proc.returncode, 0)
                self.assertNotIn(b'Traceback', err)


if __name__ == '__main__':
    unittest.main()
