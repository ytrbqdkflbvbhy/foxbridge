"""Offline checks of the new process boundary. Never opens a real Firefox account."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'FoxBridge-source'
sys.path.insert(0, str(SOURCE))
import worker
from bridge import Server


class WorkerTests(unittest.TestCase):
    def event(self, kind, data):
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            worker.emit(kind, data)
        return json.loads(capture.getvalue())['data']

    def test_account_identity_not_sent_to_ui(self):
        data = self.event('load', {'email': 'private@example.invalid', 'quota': None,
                                 'servers': [Server('DE', 'Berlin', 'example.fastly-masque.net', 443)]})
        self.assertNotIn('email', data)
        self.assertEqual(data['servers'][0]['country'], 'DE')

    def test_api_timezone_has_priority_even_if_invalid(self):
        data = self.event('check', {'iprisk': {'timezone': 'invalid/zone', 'countryCode': 'DE'}})
        self.assertEqual(data['timezone_candidate'], 'invalid/zone')
        self.assertFalse(data['timezone_estimated'])

    def test_country_fallback_matches_existing_app(self):
        data = self.event('check', {'iprisk': {'countryCode': 'US'}})
        self.assertEqual(data['timezone_candidate'], 'America/Los_Angeles')
        self.assertTrue(data['timezone_estimated'])

    def test_stdio_protocol_and_graceful_shutdown_without_qt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'profile').mkdir()
            ini = root / 'profiles.ini'
            ini.write_text('[Profile0]\nName=Offline test\nIsRelative=1\nPath=profile\n', encoding='utf-8')
            commands = [dict(operation='profiles', args={'profiles_ini': str(ini)}),
                        dict(operation='commands'), dict(operation='shutdown')]
            env = dict(os.environ, PYTHONPATH=str(SOURCE), PYTHONUTF8='1')
            result = subprocess.run([sys.executable, '-u', str(Path(worker.__file__))],
                                    input=''.join(json.dumps(c) + '\n' for c in commands),
                                    capture_output=True, encoding='utf-8', env=env, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            messages = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual([m['kind'] for m in messages], ['ready', 'profiles', 'error'])
            self.assertEqual(messages[1]['data'][0]['name'], 'Offline test')
            self.assertEqual(messages[2]['data']['operation'], 'commands')
            self.assertNotIn('PySide6', result.stderr)


if __name__ == '__main__':
    unittest.main()
