import asyncio
import base64
import json
import time
import unittest
from unittest.mock import patch

from bridge import BridgeError, LocalProxy, PassManager, Server, ServiceError, parse_authority, quota_from_headers


class FakeAccount:
    context = None

    def __init__(self):
        self.calls = 0
        self.fail = False

    def request(self, endpoint):
        self.calls += 1
        if self.fail:
            raise BridgeError('offline')
        now = int(time.time())
        body = base64.urlsafe_b64encode(json.dumps({'nbf': now - 10, 'exp': now + 600}).encode()).decode().rstrip('=')
        return {'token': f'a.{body}.signature{self.calls}'}


class PassTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_connections_share_a_pass_and_force_rotates(self):
        account = FakeAccount()
        passes = PassManager(account)
        tokens = await asyncio.gather(*(passes.get() for _ in range(20)))
        self.assertEqual(account.calls, 1)
        self.assertEqual(len(set(tokens)), 1)
        self.assertNotEqual(await passes.get(force=True), tokens[0])
        self.assertEqual(account.calls, 2)

    async def test_refresh_failure_never_uses_an_expired_pass(self):
        account = FakeAccount()
        passes = PassManager(account)
        token = await passes.get()
        passes._rotate = 0
        account.fail = True
        self.assertEqual(await passes.get(), token)
        passes._expires = 0
        with self.assertRaises(BridgeError):
            await passes.get()

    async def test_expiry_uses_actual_expiration_not_full_original_lifetime(self):
        account = FakeAccount()
        now = int(time.time())
        body = base64.urlsafe_b64encode(json.dumps({'nbf': now - 590, 'exp': now + 10}).encode()).decode().rstrip('=')
        with patch.object(account, 'request', return_value={'token': f'a.{body}.sig'}):
            passes = PassManager(account)
            await passes.get()
            self.assertLessEqual(passes._expires - time.monotonic(), 10)

    async def test_expired_or_future_pass_is_rejected(self):
        now = int(time.time())
        for claims in ({'nbf': now - 600, 'exp': now - 1}, {'nbf': now + 60, 'exp': now + 600}):
            account = FakeAccount()
            body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
            with patch.object(account, 'request', return_value={'token': f'a.{body}.sig'}):
                with self.assertRaises(ServiceError):
                    await PassManager(account).get()

    async def test_auth_rejection_invalidates_even_unexpired_cached_pass(self):
        account = FakeAccount()
        passes = PassManager(account)
        await passes.get()
        passes._rotate = 0
        with patch.object(account, 'request', side_effect=ServiceError('expired OAuth', 'auth')):
            with self.assertRaises(ServiceError):
                await passes.get()
        self.assertIsNone(passes._token)


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received = []
        self.destination = []
        self.remote = await asyncio.start_server(self.echo, '127.0.0.1', 0)
        self.remote_port = self.remote.sockets[0].getsockname()[1]
        self.proxy = LocalProxy(FakeAccount(), Server('XX', 'Test', 'test.fastly-masque.net', 443), port=0)

        async def upstream(host, port):
            self.destination.append((host, port))
            return await asyncio.open_connection('127.0.0.1', self.remote_port)

        self.proxy.upstream = upstream
        await self.proxy.start()

    async def echo(self, reader, writer):
        try:
            payload = await reader.read()
            if payload:
                self.received.append(payload)
                writer.write(b'reply:' + payload)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def asyncTearDown(self):
        await self.proxy.stop()
        self.remote.close()
        await self.remote.wait_closed()

    async def test_connect_stream_and_response_after_upload_half_close(self):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.proxy.port)
        writer.write(b'CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n')
        await writer.drain()
        self.assertIn(b'200 Connection Established', await reader.readuntil(b'\r\n\r\n'))
        writer.write(b'streaming payload')
        await writer.drain()
        writer.write_eof()
        self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'reply:streaming payload')
        writer.close()
        await writer.wait_closed()
        self.assertEqual(self.destination[-1], ('example.com', 443))

    async def test_http_request_removes_proxy_credentials(self):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.proxy.port)
        writer.write(b'GET http://example.com/path?q=1 HTTP/1.1\r\nHost: wrong.example\r\nProxy-Authorization: secret\r\n\r\n')
        await writer.drain()
        writer.write_eof()
        response = await asyncio.wait_for(reader.read(), 3)
        self.assertIn(b'GET /path?q=1 HTTP/1.1', response)
        self.assertIn(b'Host: example.com', response)
        self.assertNotIn(b'secret', response)
        self.assertNotIn(b'wrong.example', response)
        writer.close()
        await writer.wait_closed()

    async def test_upstream_failure_returns_502_and_stop_closes_port(self):
        async def fail(*args):
            raise BridgeError('upstream unavailable')
        self.proxy.upstream = fail
        reader, writer = await asyncio.open_connection('127.0.0.1', self.proxy.port)
        writer.write(b'CONNECT example.com:443 HTTP/1.1\r\n\r\n')
        await writer.drain()
        self.assertIn(b'502 Bad Gateway', await asyncio.wait_for(reader.read(), 3))
        writer.close()
        await writer.wait_closed()
        port = self.proxy.port
        await self.proxy.stop()
        with self.assertRaises(OSError):
            await asyncio.open_connection('127.0.0.1', port)

    async def test_stop_terminates_an_active_tunnel(self):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.proxy.port)
        writer.write(b'CONNECT example.com:443 HTTP/1.1\r\n\r\n')
        await writer.drain()
        await reader.readuntil(b'\r\n\r\n')
        await self.proxy.stop()
        self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'')
        self.assertEqual(self.proxy.active, 0)
        writer.close()
        await writer.wait_closed()


class InputTests(unittest.TestCase):
    def test_reject_local_addresses_and_injected_authority(self):
        for value in ('127.0.0.1:443', '[::1]:443', 'localhost:443', 'a\r\nb:443', 'user@example.com:443'):
            with self.subTest(value=value), self.assertRaises(BridgeError):
                parse_authority(value)

    def test_invalid_quota_is_not_displayed_as_real(self):
        self.assertIsNone(quota_from_headers({'X-Quota-Limit': '50', 'X-Quota-Remaining': '51'}))
        self.assertEqual(quota_from_headers({'X-Quota-Unlimited': 'true'}), {'unlimited': True})


if __name__ == '__main__':
    unittest.main()
