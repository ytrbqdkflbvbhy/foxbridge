"""Failure injection: transport never connects directly to the requested destination."""
import asyncio
import ssl
import time
import unittest
from email.utils import formatdate
from unittest.mock import patch, AsyncMock

from bridge import LocalProxy, Server, ServiceError, close_writer, retry_seconds
from test_bridge import FakeAccount


class ReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.proxy = LocalProxy(FakeAccount(), Server('DE', 'Test', 'test.fastly-masque.net', 443),
                                port=0, notify=lambda *event: self.events.append(event))
        self.proxy.mark_online()

    async def asyncTearDown(self):
        await self.proxy.stop()

    async def listen(self):
        self.proxy.listener = await asyncio.start_server(self.proxy.handle, '127.0.0.1', 0)
        self.proxy.port = self.proxy.listener.sockets[0].getsockname()[1]

    async def request(self):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.proxy.port)
        writer.write(b'CONNECT api.anthropic.com:443 HTTP/1.1\r\n\r\n')
        await writer.drain()
        result = await asyncio.wait_for(reader.read(), 2)
        await close_writer(writer)
        return result

    async def test_network_failure_dials_only_firefox_endpoint_then_gates_requests(self):
        with patch('bridge.asyncio.open_connection', new_callable=AsyncMock, side_effect=OSError('offline')) as dial:
            with self.assertRaises(ServiceError):
                await self.proxy.upstream('api.anthropic.com', 443)
            self.assertEqual(dial.await_count, 1)
            self.assertEqual(dial.call_args.args[:2], ('test.fastly-masque.net', 443))
            for _ in range(5):
                with self.assertRaises(ServiceError):
                    await self.proxy.upstream('chatgpt.com', 443)
            self.assertEqual(dial.await_count, 1)
            self.assertEqual(self.proxy.health, 'recovering')

    async def test_tls_rejection_never_retries_without_tls_validation(self):
        with patch('bridge.asyncio.open_connection', new_callable=AsyncMock,
                   side_effect=ssl.SSLCertVerificationError('bad certificate')) as dial:
            with self.assertRaises(ServiceError):
                await self.proxy.upstream('chatgpt.com', 443)
            self.assertEqual(dial.await_count, 1)
            self.assertEqual(self.proxy.failure.code, 'tls')
            self.assertGreater(self.proxy.health_info()['retry_in'], 0)

    async def test_local_listener_returns_503_until_recovered_and_reuses_same_port(self):
        await self.listen()
        port = self.proxy.port
        self.proxy.mark_failure(ServiceError('offline'))
        result = await self.request()
        self.assertIn(b'503 Service Unavailable', result)
        self.assertIn(b'Retry-After:', result)
        # Recovery is checked by the one maintenance task, not by each request.
        self.proxy.retry_now()
        remote = await asyncio.start_server(lambda r, w: w.close(), '127.0.0.1', 0)
        try:
            target_port = remote.sockets[0].getsockname()[1]
            async def recovered(*args):
                return await asyncio.open_connection('127.0.0.1', target_port)
            with patch.object(self.proxy, '_connect_upstream', side_effect=recovered):
                self.proxy.maintenance = asyncio.create_task(self.proxy.maintain())
                for _ in range(100):
                    if self.proxy.health == 'online':
                        break
                    await asyncio.sleep(.01)
                self.assertEqual(self.proxy.health, 'online')
                self.assertEqual(self.proxy.port, port)
                self.assertIn(b'200 Connection Established', await self.request())
        finally:
            remote.close()
            await remote.wait_closed()

    async def test_backoff_increases_and_concurrent_errors_do_not_postpone_retry(self):
        with patch('bridge.random.uniform', return_value=1):
            self.proxy.mark_failure(ServiceError('offline'))
            first = self.proxy.retry_at
            self.proxy.mark_failure(ServiceError('offline'))
            self.assertEqual(self.proxy.retry_at, first)
            self.proxy.mark_failure(ServiceError('offline'), recovering=True)
            self.assertGreater(self.proxy.retry_at, first + 1)
            for _ in range(10):
                self.proxy.mark_failure(ServiceError('offline'), recovering=True)
            self.assertLessEqual(self.proxy.retry_at - time.monotonic(), 60)

    async def test_retry_button_does_not_override_server_throttling(self):
        self.proxy.mark_failure(ServiceError('limited', 'rate_limit', 120))
        deadline = self.proxy.retry_at
        self.proxy.retry_now()
        self.assertEqual(self.proxy.retry_at, deadline)

    async def test_cancel_during_connect_closes_remote_socket(self):
        reader = asyncio.StreamReader()
        writer = unittest.mock.Mock()
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock()
        with patch('bridge.asyncio.open_connection', new_callable=AsyncMock, return_value=(reader, writer)):
            task = asyncio.create_task(self.proxy.upstream('chatgpt.com', 443))
            for _ in range(100):
                if writer.write.called:
                    break
                await asyncio.sleep(.001)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        writer.close.assert_called()
        self.assertEqual(self.proxy.health, 'online')

    async def test_auth_rejection_has_only_one_token_refresh_and_revokes_cached_pass(self):
        sockets = []
        async def rejected(*args, **kwargs):
            reader = asyncio.StreamReader()
            reader.feed_data(b'HTTP/1.1 407 Proxy Authentication Required\r\n\r\n')
            writer = unittest.mock.Mock()
            writer.drain, writer.wait_closed = AsyncMock(), AsyncMock()
            sockets.append(writer)
            return reader, writer
        with patch('bridge.asyncio.open_connection', side_effect=rejected) as dial:
            with self.assertRaises(ServiceError):
                await self.proxy.upstream('chatgpt.com', 443)
            self.assertEqual(dial.call_count, 2)
        self.assertEqual(self.proxy.account.calls, 2)
        self.assertEqual(self.proxy.failure.code, 'auth')
        self.assertIsNone(self.proxy.passes._token)
        for writer in sockets:
            writer.close.assert_called()
        self.assertNotIn('signature', str(self.events))

    async def test_stop_during_probe_cleanup_does_not_revive_maintenance(self):
        closing = asyncio.Event()
        async def interrupted_close(writer):
            closing.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pass
        self.proxy.mark_failure(ServiceError('offline'))
        self.proxy.retry_now()
        with patch.object(self.proxy, '_connect_upstream', new_callable=AsyncMock, return_value=(None, object())), \
                patch('bridge.close_writer', side_effect=interrupted_close):
            self.proxy.maintenance = asyncio.create_task(self.proxy.maintain())
            await asyncio.wait_for(closing.wait(), 2)
            await asyncio.wait_for(self.proxy.stop(), 2)
        self.assertEqual(self.proxy.health, 'stopped')

    def test_retry_after_accepts_http_dates_and_bounds_untrusted_values(self):
        with patch('bridge.time.time', return_value=1000000):
            self.assertEqual(retry_seconds({'Retry-After': formatdate(1000120, usegmt=True)}), 120)
        self.assertEqual(retry_seconds({'Retry-After': '999999'}), 3600)
        self.assertEqual(retry_seconds({'Retry-After': 'bad'}), 0)


if __name__ == '__main__':
    unittest.main()
