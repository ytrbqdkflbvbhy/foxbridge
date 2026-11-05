import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HAS_QT = importlib.util.find_spec('PySide6') is not None
if HAS_QT:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QCoreApplication, QEvent
    from main import Window


@unittest.skipUnless(HAS_QT, 'PySide6 is required for UI checks')
class StatisticsUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='foxbridge-ui-')
        self.window = Window(preferences_path=Path(self.folder.name) / 'settings.json', autoload=False)
        self.window.connected = True
        self.window.ready = True
        self.window.session_id = 'current'

    def tearDown(self):
        self.window.busy = False
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.folder.cleanup()

    def result(self, score=83):
        return {'session_id': 'current', 'score': score, 'raw_score': score,
                'ip': '8.8.8.8', 'ip_version': 4, 'checked_at': '2026-11-03T08:00:00+00:00',
                'trace': {'ip': '8.8.8.8', 'loc': 'DE'}, 'errors': {},
                'iprisk': {'ip': '8.8.8.8', 'trust_score': score, 'is_proxy': False,
                           'countryCode': 'DE', 'timezone': 'Europe/Berlin'},
                'geoip': {'city': 'Munich'}, 'region_adjusted': False}

    def test_error_preserves_previous_score_and_marks_it_stale(self):
        self.window.on_event('check', self.result())
        self.window.on_event('error', {'operation': 'check', 'session_id': 'current', 'message': 'HTTP 503'})
        self.assertEqual(self.window.score_label.text(), '83 / 100')
        self.assertTrue(self.window.stats_stale)
        self.assertIn('устаревший', self.window.stats_status.text())
        self.assertNotIn('8.8.8.8', self.window.stats_status.text())

    def test_late_result_from_previous_connection_is_ignored(self):
        self.window.on_event('check', self.result())
        old = self.result(99)
        old['session_id'] = 'old'
        self.window.on_event('check', old)
        self.assertEqual(self.window.score_label.text(), '83 / 100')

    def test_refresh_is_single_flight_and_does_not_disable_proxy_toggle(self):
        with patch.object(self.window.engine, 'submit') as submit:
            self.window.run('check')
            self.window.run('check')
            self.assertEqual(submit.call_count, 1)
            self.assertFalse(self.window.check.isEnabled())
            self.assertTrue(self.window.toggle.isEnabled())
            self.assertFalse(self.window.shell.isEnabled())

    def test_details_contain_full_data_and_zero_is_displayed(self):
        self.window.on_event('check', self.result(0))
        self.assertEqual(self.window.score_label.text(), '0 / 100')
        self.window.open_statistics()
        dialog = self.window.stats_dialog
        self.assertEqual(dialog.tabs.count(), 4)
        self.assertIn('8.8.8.8', dialog.raw.toPlainText())
        self.assertIn('is_proxy', dialog.raw.toPlainText())
        self.assertNotIn('Authorization', dialog.raw.toPlainText())

    def test_recovery_state_keeps_disconnect_available_and_ignores_old_session(self):
        self.window.on_event('health', {'session_id': 'old', 'state': 'recovering', 'message': 'old'})
        self.assertIsNone(self.window.channel_health)
        self.window.on_event('health', {'session_id': 'current', 'state': 'recovering',
                                      'code': 'auth', 'message': 'Войдите в Firefox.', 'retry_in': 30})
        self.assertEqual(self.window.status.text(), 'Нужен вход в Firefox')
        self.assertIn('Новые запросы', self.window.channel_note.text())
        self.window.update_controls()
        self.assertTrue(self.window.toggle.isEnabled())
        with patch.object(self.window.engine, 'submit'):
            self.window.on_event('health', {'session_id': 'current', 'state': 'online'})
        self.assertEqual(self.window.status.text(), 'Подключено')

    def test_terminal_requires_score_and_timezone_and_stale_results_lock_it(self):
        self.window.update_controls()
        self.assertFalse(self.window.shell.isEnabled())
        self.window.on_event('check', self.result(0))
        self.assertTrue(self.window.shell.isEnabled())  # Receiving a score, not its threshold, is required.
        self.assertIn('Europe/Berlin', self.window.launch_note.text())
        self.window.on_event('error', {'operation': 'check', 'session_id': 'current', 'message': 'offline'})
        self.assertFalse(self.window.shell.isEnabled())
        self.assertFalse(self.window.copy.isEnabled())

    def test_missing_or_invalid_timezone_keeps_terminal_locked(self):
        result = self.result()
        result['iprisk'] = {'ip': '8.8.8.8', 'trust_score': 83}
        self.window.on_event('check', result)
        self.assertFalse(self.window.shell.isEnabled())
        result = self.result()
        result['iprisk']['timezone'] = "Europe/Berlin'; bad"
        self.window.on_event('check', result)
        self.assertFalse(self.window.shell.isEnabled())

    def test_open_shell_guard_and_launch_receive_same_timezone(self):
        with patch('main.subprocess.Popen') as popen:
            self.window.open_shell()
            popen.assert_not_called()
            self.window.on_event('check', self.result())
            self.window.open_shell()
            self.assertEqual(popen.call_count, 1)
            self.assertEqual(popen.call_args.kwargs['env']['TZ'], 'Europe/Berlin')
            self.assertIn("$env:TZ = 'Europe/Berlin'", self.window.commands())

    def test_disconnect_and_new_session_cannot_reuse_prepared_timezone(self):
        self.window.on_event('check', self.result())
        self.window.on_event('disconnect', {})
        self.assertFalse(self.window.shell.isEnabled())
        self.window.on_event('connect', {'session_id': 'new', 'port': 8765})
        self.assertIsNone(self.window.launch_timezone)
        self.assertFalse(self.window.shell.isEnabled())


if __name__ == '__main__':
    unittest.main()
