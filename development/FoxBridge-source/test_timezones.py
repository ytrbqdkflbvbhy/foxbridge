import importlib.util
import json
import os
import shutil
import subprocess
import unittest

from launcher import proxy_environment, powershell_bootstrap
from timezones import select_timezone, validate_timezone, COUNTRY_TIMEZONES


@unittest.skipUnless(importlib.util.find_spec('PySide6'), 'Qt timezone database is required')
class TimezoneTests(unittest.TestCase):
    def test_explicit_api_timezone_wins_over_country_estimate(self):
        result = select_timezone({'iprisk': {'timezone': 'America/New_York', 'countryCode': 'US'}})
        self.assertEqual(result['id'], 'America/New_York')
        self.assertFalse(result['estimated'])

    def test_fallback_matches_site_and_is_explicitly_approximate(self):
        result = select_timezone({'iprisk': {'countryCode': 'US'}})
        self.assertEqual(result['id'], 'America/Los_Angeles')
        self.assertTrue(result['estimated'])
        self.assertEqual(COUNTRY_TIMEZONES['DE'], 'Europe/Berlin')
        self.assertEqual(COUNTRY_TIMEZONES['AR'], 'America/Argentina/Buenos_Aires')
        self.assertEqual(COUNTRY_TIMEZONES['KE'], 'Africa/Nairobi')

    def test_missing_and_invalid_api_timezone_are_not_silently_replaced(self):
        self.assertIsNone(select_timezone({})['id'])
        self.assertIsNone(select_timezone({'iprisk': {'timezone': 'Fake/Zone', 'countryCode': 'DE'}})['id'])

    def test_rejects_paths_and_shell_injection(self):
        for value in ("Europe/Berlin'; evil", '../Etc/UTC', '/etc/localtime', 'Europe/Berlin\n', 'A' * 101):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_timezone(value)

    def test_offset_is_calculated_for_the_named_zone_not_hardcoded(self):
        from PySide6.QtCore import QDateTime, QTimeZone
        result = select_timezone({'iprisk': {'timezone': 'Europe/Berlin'}})
        expected = QDateTime.currentDateTimeUtc().toTimeZone(QTimeZone(b'Europe/Berlin')).offsetFromUtc() // 60
        self.assertEqual(result['offset_minutes'], expected)

    def test_timezone_is_only_written_to_new_process_environment(self):
        old = os.environ.get('TZ')
        source = {'TZ': 'Asia/Tokyo', 'PATH': 'keep'}
        env = proxy_environment(8765, source, timezone='Europe/Berlin')
        self.assertEqual(env['TZ'], 'Europe/Berlin')
        self.assertEqual(source['TZ'], 'Asia/Tokyo')
        self.assertEqual(os.environ.get('TZ'), old)
        self.assertIn("$env:TZ = 'Europe/Berlin'", powershell_bootstrap(8765, 'Europe/Berlin'))

    @unittest.skipUnless(shutil.which('node'), 'Node.js runtime required')
    def test_installed_node_uses_selected_timezone_and_child_inherits_it(self):
        env = proxy_environment(18765, timezone='Europe/Berlin')
        script = ('const c=require("child_process"); '
                  'console.log(Intl.DateTimeFormat().resolvedOptions().timeZone); '
                  'console.log(c.execFileSync(process.execPath,["-p",'
                  '"Intl.DateTimeFormat().resolvedOptions().timeZone"],{encoding:"utf8"}).trim());')
        run = subprocess.run(['node', '-e', script], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.splitlines(), ['Europe/Berlin', 'Europe/Berlin'])


if __name__ == '__main__':
    unittest.main()
