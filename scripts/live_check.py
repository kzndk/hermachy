"""Check real RPC startup/shutdown without creating a session or sending a prompt."""
import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dist/hermachy'))
from hermachy.client import check_remote, load_client_config, _config_path, resolve_session
from hermachy.protocol import LaunchRequest, encode_request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--session', help='Also check resolution of an existing session title or ID')
args = parser.parse_args()
cwd = str(Path.cwd())
config = load_client_config(_config_path())
report = check_remote(config, cwd)
print('Doctor:', report['status'], flush=True)
if args.session:
    session = resolve_session(config, LaunchRequest(cwd, 'resolve', args.session))
    print('Named session resolves:', bool(session), flush=True)
env = dict(os.environ, HERMACHY_REQUEST=encode_request(LaunchRequest(cwd, 'gateway', connection=uuid.uuid4().hex)))
proc = subprocess.Popen([str(ROOT / 'dist/hermachy-gateway'), '-m', 'tui_gateway.entry'],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, env=env)
selector = selectors.DefaultSelector()
selector.register(proc.stdout, selectors.EVENT_READ, 'stdout')
selector.register(proc.stderr, selectors.EVENT_READ, 'stderr')
buffers = {'stdout': b'', 'stderr': b''}
try:
    proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'gateway.capabilities', 'params': {}}).encode() + b'\n')
    proc.stdin.flush()
    deadline = time.monotonic() + 30
    received = False
    while time.monotonic() < deadline and not received:
        for key, _ in selector.select(timeout=0.25):
            chunk = os.read(key.fileobj.fileno(), 65536)
            if not chunk:
                selector.unregister(key.fileobj)
                continue
            buffers[key.data] += chunk
            if key.data == 'stdout':
                while b'\n' in buffers['stdout']:
                    line, buffers['stdout'] = buffers['stdout'].split(b'\n', 1)
                    frame = json.loads(line)
                    if frame.get('id') == 1:
                        if 'error' in frame:
                            raise RuntimeError('Gateway returned an RPC error: ' + str(frame['error']))
                        assert 'result' in frame
                        received = True
        if proc.poll() is not None and not received:
            break
    if not received:
        raise RuntimeError('Gateway did not answer capability RPC; stderr: ' + buffers['stderr'][-2000:].decode(errors='replace'))
    print('Live gateway capability RPC: passed', flush=True)
    proc.stdin.close()
    proc.stdin = None
    _, stderr = proc.communicate(timeout=15)
    assert proc.returncode == 0, (proc.returncode, stderr.decode(errors='replace'))
    print('Live gateway EOF shutdown: passed', flush=True)
finally:
    selector.close()
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=12)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
