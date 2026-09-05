"""Run the forced server and its real child process against a disposable session DB."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from hermes_link.protocol import LaunchRequest, encode_request
from hermes_link.server import SAFE_EXECUTOR_CAPABILITY_VERSION
from scripts.build_zipapps import build_one


class ResolutionTests(unittest.TestCase):
    def test_workspace_cli_session_precedes_unrelated_tui_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / 'runtime'
            bin_dir = runtime / 'venv/bin'
            bin_dir.mkdir(parents=True)
            (bin_dir / 'python').symlink_to(sys.executable)
            (bin_dir / 'hermes').write_text('#!/bin/sh\nexit 0\n')
            (bin_dir / 'hermes').chmod(0o700)
            module = runtime / 'tools/environments'
            module.mkdir(parents=True)
            (module.parent / '__init__.py').touch()
            (module / '__init__.py').touch()
            (module / 'ssh.py').write_text(f'HERMES_LINK_CONTROL_VERSION = {SAFE_EXECUTOR_CAPABILITY_VERSION!r}\n')
            (runtime / 'hermes_state.py').write_text('''
class SessionDB:
    def search_sessions(self, source, limit, workspace_key=None):
        if source == 'cli' and workspace_key == '/workspace':
            return [{'id': 'workspace-cli'}]
        if source == 'tui' and workspace_key is None:
            return [{'id': 'unrelated-tui'}]
        return []
    def get_compression_tip(self, sid): return sid
    def close(self): pass
''')
            key = root / 'key'
            key.write_text('test-only')
            key.chmod(0o600)
            config = root / 'server.json'
            config.write_text(json.dumps({
                'hermes_command': str(bin_dir / 'hermes'),
                'hermes_source_root': str(runtime), 'executor_host': 'host',
                'executor_user': 'user', 'executor_identity_file': str(key),
            }))
            config.chmod(0o600)
            artifact = build_one('hlink-server', root / 'dist')
            request = LaunchRequest('/workspace', 'resolve')
            env = dict(os.environ, HOME=str(root), HLINK_SERVER_CONFIG=str(config),
                       SSH_ORIGINAL_COMMAND='hlink-v1 ' + encode_request(request))
            result = subprocess.run([sys.executable, str(artifact)], env=env,
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['session'], 'workspace-cli')
