"""Offline checks of the new process boundary. Never opens a real Firefox account."""
import contextlib
import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

SOURCE = Path(__file__).resolve().parents[2] / 'FoxBridge-source'
sys.path.insert(0, str(SOURCE))
import worker
from bridge import Server


class WorkerTests(unittest.TestCase):
    def test_tun_checks_before_start_and_rolls_back_failed_check(self):
        for score, succeeds in ((72, True), (0, True), (None, False)):
            with self.subTest(score=score):
                order = []
                loop = asyncio.new_event_loop()
                thread = threading.Thread(target=loop.run_forever, daemon=True)
                thread.start()
                proxy = SimpleNamespace(health='online', session_id='test-session')
                engine = SimpleNamespace(proxy=None, servers=[Server('DE', 'Berlin', 'vpn.invalid', 443)], loop=loop)
                async def connect(*args):
                    order.append('connect')
                    engine.proxy = proxy
                    return {'session_id': 'test-session', 'port': 8765}
                async def check(**kwargs):
                    order.append('check')
                    return {'session_id': 'test-session', 'ip': '192.0.2.1', 'score': score}
                async def disconnect():
                    order.append('disconnect')
                    engine.proxy = None
                engine.connect, engine.check, engine.disconnect = connect, check, disconnect
                engine.shutdown = lambda: loop.call_soon_threadsafe(loop.stop)
                controller = Mock()
                controller.start.side_effect = lambda port: order.append('tun')
                commands = io.StringIO(json.dumps({'operation': 'connect', 'args': {
                    'tun': True, 'server_index': 0, 'port': 8765}}) + '\n')
                try:
                    with patch.object(worker, 'Engine', return_value=engine), \
                         patch.object(worker, 'TunController', return_value=controller), \
                         patch.object(worker, 'is_admin', return_value=True), \
                         patch.object(worker, 'enable_worker_bypass'), patch.object(worker, 'disable_worker_bypass'), \
                         patch.object(worker.sys, 'stdin', commands), patch.object(worker, 'emit') as emit:
                        worker.main()
                    if succeeds:
                        self.assertEqual(order, ['connect', 'check', 'tun'])
                        controller.start.assert_called_once_with(8765)
                        events = [c.args[0] for c in emit.call_args_list]
                        self.assertLess(events.index('connect'), events.index('check'))
                    else:
                        self.assertEqual(order, ['connect', 'check', 'disconnect'])
                        controller.start.assert_not_called()
                        self.assertIn('TUN не включён', emit.call_args_list[-1].args[1]['message'])
                finally:
                    loop.call_soon_threadsafe(loop.stop)
                    thread.join(timeout=5)
                    loop.close()

    def test_tun_requires_admin_before_any_profile_or_connection_access(self):
        capture = io.StringIO()
        commands = io.StringIO(json.dumps({'operation': 'connect', 'args': {'tun': True,
                                'server_index': 0, 'port': 8765}}) + '\n')
        with patch.object(worker, 'Engine') as engine, patch.object(worker, 'is_admin', return_value=False), \
             patch.object(worker.sys, 'stdin', commands), contextlib.redirect_stdout(capture):
            engine.return_value.proxy = None
            worker.main()
            engine.return_value.connect.assert_not_called()
            engine.return_value.shutdown.assert_called_once()
        messages = [json.loads(line) for line in capture.getvalue().splitlines()]
        self.assertEqual(messages[-1]['kind'], 'error')
        self.assertIn('администратора', messages[-1]['data']['message'])

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


class ActivityPublisherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = asyncio.Queue()
        self.proxy = SimpleNamespace(session_id='current', health='online',
                                     activity_changed=asyncio.Event(), up=0, down=0, active=0)
        self.engine = SimpleNamespace(proxy=self.proxy)
        self.engine.stats = lambda: dict(up=self.proxy.up, down=self.proxy.down,
                                         active=self.proxy.active, uptime=0)
        self.publisher = worker.ActivityPublisher(self.engine)
        self.emit_patch = patch.object(worker, 'emit', side_effect=lambda kind, data:
                                       self.events.put_nowait((kind, data)))
        self.emit_patch.start()

    async def asyncTearDown(self):
        await self.publisher.subscribe()
        self.emit_patch.stop()

    async def next_event(self):
        return await asyncio.wait_for(self.events.get(), 3)

    async def test_initial_snapshot_then_no_idle_updates(self):
        await self.publisher.subscribe('current', 2)
        kind, data = await self.next_event()
        self.assertEqual((kind, data['session_id']), ('activity', 'current'))
        # Wait beyond the rate limit: an idle stream must stay asleep.
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(self.events.get(), 2.1)

    async def test_burst_coalesces_without_losing_final_counters(self):
        await self.publisher.subscribe('current', 2)
        await self.next_event()
        for count in range(1, 101):
            self.proxy.down = count
            self.proxy.activity_changed.set()
        self.assertTrue(self.events.empty())
        _, data = await self.next_event()
        self.assertEqual(data['down'], 100)
        self.assertTrue(self.events.empty())
        # A change after the publisher is already waiting must wake it too.
        await asyncio.sleep(2.1)
        self.proxy.active = 1
        self.proxy.activity_changed.set()
        _, data = await self.next_event()
        self.assertEqual(data['active'], 1)

    async def test_unsubscribe_cancels_stream_and_resume_gets_fresh_snapshot(self):
        await self.publisher.subscribe('current', 2)
        await self.next_event()
        task = self.publisher.task
        await self.publisher.subscribe()
        self.assertTrue(task.done())
        self.assertIsNone(self.publisher.task)
        self.proxy.up = 123
        self.proxy.activity_changed.set()
        self.assertTrue(self.events.empty())
        await self.publisher.subscribe('current', 2)
        _, data = await self.next_event()
        self.assertEqual(data['up'], 123)

    async def test_inactive_subscription_replaces_instead_of_duplicating_task(self):
        await self.publisher.subscribe('current', 2)
        await self.next_event()
        previous = self.publisher.task
        await self.publisher.subscribe('current', 10)
        await self.next_event()
        self.assertTrue(previous.done())
        self.assertIsNot(previous, self.publisher.task)

    async def test_stale_session_and_invalid_interval_preserve_current_stream(self):
        await self.publisher.subscribe('current', 2)
        await self.next_event()
        task = self.publisher.task
        await self.publisher.subscribe('old', 2)
        self.assertIs(self.publisher.task, task)
        with self.assertRaises(worker.BridgeError):
            await self.publisher.subscribe('current', float('nan'))
        self.assertIs(self.publisher.task, task)
        self.assertFalse(task.done())

    async def test_stopped_proxy_wakes_and_finishes_waiting_publisher(self):
        await self.publisher.subscribe('current', 2)
        await self.next_event()
        await asyncio.sleep(2.1)
        self.proxy.health = 'stopped'
        self.proxy.activity_changed.set()
        await asyncio.wait_for(self.publisher.task, 1)
        self.assertTrue(self.events.empty())


if __name__ == '__main__':
    unittest.main()
