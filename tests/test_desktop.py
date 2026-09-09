"""The GUI must preserve the CLI's remote capability gate."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hermachy import desktop
from hermachy.client import ClientConfig
from hermachy.protocol import decode_request


class DesktopBridgeTests(unittest.TestCase):
    def test_unsupported_remote_never_starts_gateway(self):
        with tempfile.TemporaryDirectory() as root:
            identity = Path(root) / 'identity'
            identity.touch()
            with patch('sys.argv', ['bridge', '--cwd', root]), \
                 patch.object(desktop, 'load_client_config', return_value=ClientConfig('host', 'user', identity)), \
                 patch.object(desktop, 'check_remote', return_value={'persistent_gateway': 0}), \
                 patch('hermachy.gateway.main') as gateway, \
                 contextlib.redirect_stdout(io.StringIO()) as output, \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(desktop.main(), 2)
                gateway.assert_not_called()
                self.assertEqual(output.getvalue(), '')

    def test_ready_only_after_checks_and_owned_launch_environment(self):
        with tempfile.TemporaryDirectory() as root:
            identity = Path(root) / 'identity'
            identity.touch()
            report = {'persistent_gateway': 1, 'execution_targets': ['omarchy', 'hermes'],
                      'studio': {'hostname': 'test-hermes'}, 'version': '0.4.0'}
            with patch('sys.argv', ['bridge', '--cwd', root]), \
                 patch.dict(os.environ, {'HERMES_YOLO_MODE': '1', 'HERMES_HOME': '/unrelated'}, clear=True), \
                 patch.object(desktop, 'load_client_config', return_value=ClientConfig('host', 'user', identity)), \
                 patch.object(desktop, 'check_remote', return_value=report) as check, \
                 patch('hermachy.gateway.main', return_value=0) as gateway, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(desktop.main(), 0)
                check.assert_called_once()
                gateway.assert_called_once()
                self.assertNotIn('HERMES_YOLO_MODE', os.environ)
                self.assertNotIn('HERMES_HOME', os.environ)
                request = decode_request(os.environ['HERMACHY_REQUEST'])
                self.assertEqual(request.cwd, root)
                self.assertEqual(request.action, 'gateway')
                self.assertEqual(len(request.connection), 32)
                self.assertEqual(json.loads(output.getvalue())['method'], 'desktop.ready')


if __name__ == '__main__':
    unittest.main()
