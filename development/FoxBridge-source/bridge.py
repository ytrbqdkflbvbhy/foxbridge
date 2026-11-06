"""Firefox VPN account adapter and loopback HTTP CONNECT proxy.

No browser modification, no password handling, no direct destination fallback.
"""
from __future__ import annotations

import asyncio
import base64
import configparser
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import random
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from netcoffee import NetCoffeeError, collect_statistics

GUARDIAN = 'https://vpn.mozilla.org/api/v1/fpn/'
SCOPE = {'profile', 'https://identity.mozilla.com/apps/vpn'}
CA_SHA256 = '796f1389bfea13c0ed78f663b765baaff54a508e5483786bd6ece73c5e3e3cbe'


class BridgeError(Exception):
    pass


async def open_tls_connection(host, port, context):
    return await asyncio.open_connection(host, port, ssl=context, server_hostname=host,
                                         limit=32768, ssl_handshake_timeout=12)


class ServiceError(BridgeError):
    """A failure of the Mozilla transport/control plane, never a destination fallback."""
    def __init__(self, message, code='network', retry_after=0):
        super().__init__(message)
        self.code = code
        self.retry_after = max(0, min(3600, retry_after))


def service_error(error):
    if isinstance(error, ServiceError):
        return error
    if isinstance(error, ssl.SSLCertVerificationError):
        return ServiceError('Сертификат сервера не прошёл проверку. Соединение остановлено.', 'tls', 60)
    return ServiceError(explain_error(error), 'network')


def retry_seconds(headers):
    value = headers.get('Retry-After', '')
    if value.isdigit():
        return min(int(value), 3600)
    try:
        return max(0, min(3600, int(parsedate_to_datetime(value).timestamp() - time.time() + .999)))
    except (ValueError, TypeError, OverflowError):
        return 0


def explain_error(error):
    if isinstance(error, (BridgeError, NetCoffeeError)):
        return str(error)
    if isinstance(error, ssl.SSLCertVerificationError):
        return 'Сертификат TLS не прошёл проверку. Соединение остановлено.'
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return 'Сервер не ответил вовремя. Попробуйте другой сервер.'
    if isinstance(error, OSError) and getattr(error, 'winerror', None) == 10048:
        return 'Локальный порт уже занят. Выберите другой порт.'
    if isinstance(error, (ConnectionError, OSError, urllib.error.URLError)):
        return 'Ошибка сети. Проверьте интернет и доступность сервера.'
    return f'Ошибка операции ({type(error).__name__}).'


def tls_context():
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    bundle = (base / 'cacert.pem').read_bytes()
    if hashlib.sha256(bundle).hexdigest() != CA_SHA256:
        raise BridgeError('Файл доверенных сертификатов повреждён или заменён.')
    pem = '\n'.join(re.findall(
        r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----',
        bundle.decode('utf-8'), re.S))
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=pem)
    context.set_alpn_protocols(['http/1.1'])
    return context


def read_json(path):
    for attempt in range(3):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, PermissionError):
            if attempt == 2:
                raise BridgeError('Firefox обновляет профиль. Повторите через несколько секунд.')
            time.sleep(.1)


def firefox_profiles(profiles_ini=None):
    ini_path = (Path(os.path.expandvars(str(profiles_ini))).expanduser() if profiles_ini else
                Path(os.environ.get('APPDATA', '')) / 'Mozilla/Firefox/profiles.ini')
    root = ini_path.resolve().parent
    ini = configparser.ConfigParser()
    try:
        found = ini.read(ini_path, encoding='utf-8-sig')
    except (OSError, UnicodeError, configparser.Error):
        raise BridgeError('Не удалось прочитать profiles.ini. Проверьте выбранный файл в настройках.')
    if not found:
        raise BridgeError('Firefox не найден. Укажите файл profiles.ini в настройках.')
    preferred = {ini[s].get('Default') for s in ini.sections() if s.startswith('Install')}
    result = []
    for section in ini.sections():
        if not section.startswith('Profile'):
            continue
        value = ini[section].get('Path', '')
        if not value:
            continue
        path = root / value if ini[section].get('IsRelative', '1') == '1' else Path(value)
        if path.is_dir():
            result.append((ini[section].get('Name', section), path, value in preferred))
    if not result:
        raise BridgeError('В profiles.ini нет существующих профилей. Проверьте файл и пути к профилям.')
    return sorted(result, key=lambda item: not item[2])


def read_prefs(path):
    prefs = {}
    try:
        text = (path / 'prefs.js').read_text(encoding='utf-8')
    except OSError:
        raise BridgeError('Не удалось прочитать настройки профиля Firefox.')
    for line in text.splitlines():
        match = re.fullmatch(r'user_pref\(("(?:[^"\\]|\\.)*"), (.*)\);', line)
        if match:
            try:
                prefs[json.loads(match[1])] = json.loads(match[2])
            except ValueError:
                pass
    endpoint = prefs.get('browser.ipProtection.guardian.endpoint', 'https://vpn.mozilla.org/')
    if endpoint.rstrip('/') != 'https://vpn.mozilla.org':
        raise BridgeError('В Firefox настроен нестандартный сервер авторизации. Подключение остановлено.')
    return prefs


@dataclass(frozen=True)
class Server:
    country: str
    city: str
    host: str
    port: int
    locked: bool = False

    @property
    def label(self):
        country = 'Автоматически' if self.country == 'REC' else self.country
        return f'{country} · {self.city} · {self.host}'


def parse_servers(locations):
    result = []
    for country in locations:
        for city in country.get('cities', []):
            for server in city.get('servers', []):
                if server.get('quarantined'):
                    continue
                protocols = server.get('protocols') or [dict(
                    name='connect', scheme='https', host=server.get('hostname'),
                    port=server.get('port', 443))]
                for protocol in protocols:
                    host = protocol.get('host', '')
                    port = int(protocol.get('port', 443))
                    if (protocol.get('name') == 'connect'
                            and protocol.get('scheme', 'https') == 'https'
                            and re.fullmatch(r'[A-Za-z0-9.-]+', host)
                            and host.endswith('.fastly-masque.net') and 0 < port < 65536):
                        item = Server(country.get('code', ''), city.get('name', ''),
                                      host, port, bool(country.get('locked')))
                        if item not in result:
                            result.append(item)
    return result


def quota_from_headers(headers):
    if headers.get('X-Quota-Unlimited', '').lower() == 'true':
        return {'unlimited': True}
    try:
        maximum = int(headers['X-Quota-Limit'])
        remaining = int(headers['X-Quota-Remaining'])
        if maximum < 0 or not 0 <= remaining <= maximum:
            return None
        return {'unlimited': False, 'max': maximum, 'remaining': remaining,
                'reset': headers.get('X-Quota-Reset', '')}
    except (KeyError, TypeError, ValueError):
        return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Account:
    def __init__(self, profile):
        self.profile = Path(profile)
        self.context = tls_context()
        self.quota = None
        self.subscribed = False

    def oauth(self):
        try:
            account = read_json(self.profile / 'signedInUser.json')['accountData']
            tokens = account.get('oauthTokens', {})
            return next(v['token'] for k, v in tokens.items() if set(k.split('|')) == SCOPE)
        except (OSError, KeyError, StopIteration):
            raise ServiceError('Нет VPN-авторизации. Включите встроенный VPN в Firefox.', 'auth', 30)

    def request(self, endpoint, method='GET'):
        if endpoint not in ('token', 'status'):
            raise ValueError('Unsupported Guardian operation')
        request = urllib.request.Request(GUARDIAN + endpoint, method=method, headers={
            'Authorization': 'Bearer ' + self.oauth(), 'Content-Type': 'application/json',
            'User-Agent': 'FoxBridge/0.6 (local educational prototype)'})
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=self.context))
        try:
            with opener.open(request, timeout=15) as response:
                quota = quota_from_headers(response.headers)
                if quota is not None:
                    self.quota = quota
                payload = response.read(262145) if method != 'HEAD' else b''
                if len(payload) > 262144:
                    raise BridgeError('Слишком большой ответ сервера авторизации.')
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as error:
            quota = quota_from_headers(error.headers)
            if quota is not None:
                self.quota = quota
            messages = {
                401: 'Авторизация устарела. Включите VPN в Firefox и обновите профиль.',
                403: 'У аккаунта нет доступа к встроенному VPN.',
                429: 'Квота исчерпана или сервер ограничил частоту запросов. Повторите позже.',
                451: 'Сервис Mozilla недоступен для текущего региона.'}
            code = ('auth' if error.code in (401, 403, 451) else
                    'quota' if self.quota and not self.quota.get('unlimited') and self.quota.get('remaining') == 0
                    else 'rate_limit' if error.code == 429 else 'service')
            raise ServiceError(messages.get(error.code, f'Mozilla вернула HTTP {error.code}.'),
                               code, retry_seconds(error.headers)) from None
        except (OSError, urllib.error.URLError) as error:
            cause = getattr(error, 'reason', error)
            raise service_error(cause) from None
        except (ValueError, UnicodeError):
            raise ServiceError('Mozilla вернула некорректный ответ.', 'protocol', 30) from None

    def info(self):
        info = self.request('status')
        self.subscribed = bool(info.get('subscribed'))
        self.request('token', 'HEAD')
        return self.quota


class PassManager:
    def __init__(self, account):
        self.account = account
        self._token = None
        self._expires = 0
        self._rotate = 0
        self._retry = 0
        self._last_error = None
        self._lock = asyncio.Lock()

    async def get(self, force=False):
        async with self._lock:
            now = time.monotonic()
            if self._token and not force and now < self._rotate:
                return self._token
            if now < self._retry:
                if self._token and now < self._expires and not force:
                    return self._token
                raise self._last_error or ServiceError('Ожидание повторной авторизации прокси.', 'auth', 30)
            try:
                payload = await asyncio.to_thread(self.account.request, 'token')
                token = payload.get('token', '')
                if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_=-]+\.[A-Za-z0-9_=-]+\.[A-Za-z0-9_=-]+', token):
                    raise ServiceError('Mozilla вернула некорректный пропуск прокси.', 'protocol', 30)
                part = token.split('.')[1]
                try:
                    claims = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
                    if not isinstance(claims, dict):
                        raise ValueError('Invalid claims')
                except (ValueError, UnicodeError):
                    raise ServiceError('Mozilla вернула некорректный пропуск прокси.', 'protocol', 30) from None
                # JWT lifetime is scheduling information, never used to bypass server verification.
                try:
                    expires, not_before = int(claims['exp']), int(claims['nbf'])
                except (KeyError, TypeError, ValueError):
                    raise ServiceError('Некорректный срок действия пропуска.', 'protocol', 30) from None
                wall_now = time.time()
                lifetime = expires - wall_now
                if not 0 < lifetime < 604800 or not_before > wall_now + 30 or expires <= not_before:
                    raise ServiceError('Пропуск истёк или ещё не действует. Проверьте часы Windows.', 'auth', 30)
                now = time.monotonic()
                self._token, self._expires = token, now + lifetime
                self._rotate = now + max(1, lifetime - min(120, lifetime / 3))
                self._retry = 0
                self._last_error = None
                return token
            except Exception as error:
                self._last_error = error
                self._retry = time.monotonic() + 30
                # An explicit authorization/quota rejection revokes the cached pass.
                revoked = isinstance(error, ServiceError) and error.code in ('auth', 'quota')
                if revoked:
                    self._token, self._expires = None, 0
                if self._token and time.monotonic() < self._expires and not force and not revoked:
                    return self._token
                raise


def parse_authority(authority, default_port=None):
    if any(c in authority for c in '\r\n\x00/@?#\\'):
        raise BridgeError('Некорректный адрес назначения.')
    parsed = urllib.parse.urlsplit('//' + authority)
    try:
        host, port = parsed.hostname, parsed.port or default_port
    except ValueError:
        raise BridgeError('Некорректный порт назначения.')
    if not host or port is None or not 1 <= port <= 65535:
        raise BridgeError('Некорректный адрес назначения.')
    host = host.rstrip('.').encode('idna').decode('ascii')
    if host == 'localhost' or host.endswith(('.localhost', '.local')):
        raise BridgeError('Локальные адреса не направляются в удалённый прокси.')
    try:
        if not ipaddress.ip_address(host).is_global:
            raise BridgeError('Локальные адреса не направляются в удалённый прокси.')
    except ValueError:
        if not re.fullmatch(r'[A-Za-z0-9.-]+', host):
            raise BridgeError('Некорректное имя сервера.')
    return host, port


async def close_writer(writer):
    if writer:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 2)
        except (Exception, asyncio.CancelledError):
            pass


class LocalProxy:
    def __init__(self, account, server, port=8765, notify=lambda *_: None):
        self.account, self.endpoint, self.port = account, server, port
        self.passes = PassManager(account)
        self.context = account.context
        self.notify = notify
        self.listener = None
        self.tasks = set()
        self.maintenance = None
        self.up = self.down = self.active = 0
        self.since = None
        self.session_id = uuid.uuid4().hex
        self.health = 'starting'
        self.failure = None
        self.failures = 0
        self.retry_at = 0
        self.wakeup = asyncio.Event()

    def health_info(self):
        return {'session_id': self.session_id, 'state': self.health,
                'code': self.failure.code if self.failure else None,
                'message': str(self.failure) if self.failure else '',
                'retry_in': max(0, int(self.retry_at - time.monotonic() + .999)),
                'attempts': self.failures}

    def mark_failure(self, error, recovering=False):
        if self.health == 'stopped':
            return
        if self.health == 'recovering' and not recovering:
            return  # Concurrent failures must not postpone the scheduled probe.
        self.failure = service_error(error)
        if self.failure.code in ('auth', 'quota'):
            self.passes._token, self.passes._expires = None, 0
        self.failures += 1
        delay = min(60, 2 ** min(self.failures, 6)) * random.uniform(.8, 1)
        minimum = {'auth': 30, 'quota': 60, 'tls': 60, 'rate_limit': 30, 'protocol': 30}
        delay = max(delay, minimum.get(self.failure.code, 0), self.failure.retry_after)
        self.retry_at = time.monotonic() + delay
        self.health = 'recovering'
        self.notify('health', self.health_info())
        self.wakeup.set()

    def mark_online(self):
        self.health = 'online'
        self.failure = None
        self.failures = 0
        self.retry_at = 0
        self.notify('health', self.health_info())

    def retry_now(self):
        if self.health == 'recovering':
            # Respect service throttling even when the button is clicked repeatedly.
            if self.failure.code in ('quota', 'rate_limit') and time.monotonic() < self.retry_at:
                return
            self.retry_at = 0
            self.passes._retry = 0
            self.wakeup.set()

    async def upstream(self, host, port):
        if self.health == 'recovering':
            raise ServiceError('Канал Firefox восстанавливается. Прямой доступ не используется.',
                               'recovering', self.health_info()['retry_in'])
        if self.health == 'stopped':
            raise ServiceError('Прокси выключен.', 'stopped')
        try:
            return await self._connect_upstream(host, port)
        except ServiceError as error:
            self.mark_failure(error)
            raise

    async def _connect_upstream(self, host, port):
        authority = f'[{host}]:{port}' if ':' in host else f'{host}:{port}'
        for attempt in range(2):
            writer = None
            try:
                token = await self.passes.get(force=bool(attempt))
                reader, writer = await asyncio.wait_for(open_tls_connection(
                    self.endpoint.host, self.endpoint.port, self.context), 15)
                writer.write((f'CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n'
                              f'Proxy-Authorization: Bearer {token}\r\n\r\n').encode('ascii'))
                await writer.drain()
                header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 15)
                try:
                    status_line = header.split(b'\r\n', 1)[0].split(b' ', 2)
                    if status_line[0] not in (b'HTTP/1.0', b'HTTP/1.1'):
                        raise ValueError('Invalid protocol')
                    status = int(status_line[1])
                except (ValueError, IndexError):
                    raise ServiceError('Прокси Firefox вернул некорректный ответ.', 'protocol', 30) from None
                if status == 200:
                    return reader, writer
                if status not in (401, 407) or attempt:
                    if status in (401, 407, 429) or 500 <= status <= 599:
                        code = 'auth' if status in (401, 407) else 'rate_limit' if status == 429 else 'service'
                        headers = dict(line.split(':', 1) for line in header.decode('iso-8859-1').split('\r\n')[1:]
                                       if ':' in line)
                        wait = next((v.strip() for k, v in headers.items() if k.lower() == 'retry-after'), '')
                        raise ServiceError(f'Прокси Firefox отклонил соединение: HTTP {status}.', code,
                                           retry_seconds({'Retry-After': wait}))
                    raise BridgeError(f'Прокси Mozilla отклонил соединение: HTTP {status}.')
            except asyncio.CancelledError:
                await close_writer(writer)
                raise
            except (OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as error:
                await close_writer(writer)
                raise service_error(error) from None
            except Exception:
                await close_writer(writer)
                raise
            await close_writer(writer)
        raise BridgeError('Не удалось авторизоваться на прокси.')

    async def start(self):
        await self.passes.get()
        # Verify TLS + authenticated CONNECT before opening the local listener.
        _, test_writer = await self.upstream('example.com', 443)
        await close_writer(test_writer)
        self.listener = await asyncio.start_server(self.handle, '127.0.0.1', self.port, limit=32768)
        self.port = self.listener.sockets[0].getsockname()[1]
        self.since = time.monotonic()
        self.mark_online()
        self.maintenance = asyncio.create_task(self.maintain())

    async def stop(self):
        self.health = 'stopped'
        listener, self.listener = self.listener, None
        if listener:
            listener.close()
        if self.maintenance:
            self.maintenance.cancel()
            await asyncio.gather(self.maintenance, return_exceptions=True)
            self.maintenance = None
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
        if listener:
            await listener.wait_closed()
        self.passes._token = None

    async def maintain(self):
        last_quota = time.monotonic()
        while self.health != 'stopped':
            delay = max(0, self.retry_at - time.monotonic()) if self.health == 'recovering' else 30
            self.wakeup.clear()
            try:
                await asyncio.wait_for(self.wakeup.wait(), delay)
                continue
            except TimeoutError:
                pass
            try:
                # A single probe owns recovery; incoming requests never bypass the gate.
                _, writer = await self._connect_upstream('example.com', 443)
                await close_writer(writer)
                if self.health == 'stopped':
                    return
                if time.monotonic() - last_quota >= 60:
                    await asyncio.to_thread(self.account.request, 'token', 'HEAD')
                    self.notify('quota', self.account.quota)
                    last_quota = time.monotonic()
                    quota = self.account.quota
                    if quota and not quota.get('unlimited') and quota.get('remaining') == 0:
                        raise ServiceError('Квота Mozilla исчерпана. Ожидаем доступный лимит.', 'quota', 60)
                if self.health != 'online':
                    self.mark_online()
            except Exception as error:
                self.mark_failure(error, recovering=True)

    async def pipe(self, reader, writer, upload):
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
            if upload:
                self.up += len(chunk)
            else:
                self.down += len(chunk)
        if writer.can_write_eof():
            writer.write_eof()

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        remote = None
        pipes = []
        established = False
        try:
            if len(self.tasks) > 128:
                raise BridgeError('Достигнут лимит одновременных соединений.')
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 15)
            if len(header) > 16384:
                raise BridgeError('Слишком большой заголовок запроса.')
            lines = header.decode('iso-8859-1').split('\r\n')
            method, target, version = lines[0].split(' ')
            if version not in ('HTTP/1.0', 'HTTP/1.1'):
                raise BridgeError('Поддерживается HTTP/1.1 CONNECT.')
            fields = []
            for line in lines[1:]:
                if not line:
                    continue
                name, sep, value = line.partition(':')
                if not sep or not re.fullmatch(r'[A-Za-z0-9!#$%&\x27*+.^_`|~-]+', name):
                    raise BridgeError('Некорректный HTTP-заголовок.')
                fields.append((name, value.strip()))
            if method == 'CONNECT':
                host, port = parse_authority(target)
                request = None
            else:
                url = urllib.parse.urlsplit(target)
                if url.scheme != 'http' or not url.netloc:
                    raise BridgeError('Для HTTPS используйте CONNECT, для HTTP — абсолютный URL.')
                host, port = parse_authority(url.netloc, 80)
                names = [name.lower() for name, _ in fields]
                if names.count('content-length') > 1 or ('content-length' in names and 'transfer-encoding' in names):
                    raise BridgeError('Неоднозначная длина HTTP-запроса.')
                path = urllib.parse.urlunsplit(('', '', url.path or '/', url.query, ''))
                kept = [(k, v) for k, v in fields if k.lower() not in
                        ('proxy-authorization', 'proxy-connection', 'host', 'connection')]
                kept.extend([('Host', url.netloc), ('Connection', 'close')])
                request = (f'{method} {path} {version}\r\n' +
                           ''.join(f'{k}: {v}\r\n' for k, v in kept) + '\r\n').encode('iso-8859-1')
            upstream_reader, remote = await self.upstream(host, port)
            if request is None:
                writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                await writer.drain()
            else:
                remote.write(request)
                await remote.drain()
            established = True
            self.active += 1
            pipes = [asyncio.create_task(self.pipe(reader, remote, True)),
                     asyncio.create_task(self.pipe(upstream_reader, writer, False))]
            # TLS upstream cannot half-close: retain download after local upload EOF.
            done, pending = await asyncio.wait(pipes, return_when=asyncio.FIRST_COMPLETED)
            if pipes[0] in done and not pipes[0].cancelled() and pipes[0].exception() is None:
                await pipes[1]
            for future in done:
                if not future.cancelled():
                    future.result()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if not established:
                try:
                    wait = self.health_info()['retry_in']
                    response = ('HTTP/1.1 503 Service Unavailable\r\n' + f'Retry-After: {max(1, wait)}\r\n'
                                if isinstance(error, ServiceError) else 'HTTP/1.1 502 Bad Gateway\r\n')
                    writer.write((response + 'Connection: close\r\nContent-Length: 0\r\n\r\n').encode('ascii'))
                    await writer.drain()
                except OSError:
                    pass
            if isinstance(error, BridgeError) and not isinstance(error, ServiceError):
                self.notify('warning', explain_error(error))
        finally:
            for future in pipes:
                if not future.done():
                    future.cancel()
            if pipes:
                await asyncio.gather(*pipes, return_exceptions=True)
            if established:
                self.active -= 1
            await close_writer(remote)
            await close_writer(writer)
            self.tasks.discard(task)


class Engine:
    def __init__(self, notify):
        self.notify = notify
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.proxy = None
        self.account = None
        self.servers = []

    def submit(self, operation, **kwargs):
        return asyncio.run_coroutine_threadsafe(self.execute(operation, **kwargs), self.loop)

    async def execute(self, operation, **kwargs):
        try:
            result = await getattr(self, operation)(**kwargs)
            self.notify(operation, result)
            return result
        except Exception as error:
            self.notify('error', {'operation': operation, 'message': explain_error(error),
                                  'session_id': kwargs.get('session_id'),
                                  'retry_after': getattr(error, 'retry_after', 0)})
            return None

    async def load(self, profile):
        if self.proxy:
            raise BridgeError('Сначала отключите прокси перед сменой профиля.')
        path = Path(profile)
        prefs = await asyncio.to_thread(read_prefs, path)
        self.account = Account(path)
        quota = await asyncio.to_thread(self.account.info)
        try:
            locations = json.loads(prefs.get('browser.ipProtection.locationListCache', '[]'))
        except ValueError:
            locations = []
        self.servers = [s for s in parse_servers(locations) if not s.locked or self.account.subscribed]
        if not self.servers:
            raise BridgeError('В профиле нет доступных CONNECT-серверов. Включите VPN в Firefox и обновите профиль.')
        account = await asyncio.to_thread(read_json, path / 'signedInUser.json')
        return {'servers': self.servers, 'quota': quota,
                'email': account.get('accountData', {}).get('email', ''),
                'selected_country': prefs.get('browser.ipProtection.egressLocation', 'REC')}

    async def connect(self, server_index, port):
        if self.proxy or self.account is None:
            raise BridgeError('Профиль не готов или подключение уже работает.')
        if not 1024 <= port <= 65535:
            raise BridgeError('Выберите порт от 1024 до 65535.')
        proxy = LocalProxy(self.account, self.servers[server_index], port, self.notify)
        try:
            await proxy.start()
        except BaseException:
            await proxy.stop()
            raise
        self.proxy = proxy
        return {'port': proxy.port, 'host': proxy.endpoint.host, 'session_id': proxy.session_id,
                'health': proxy.health_info()}

    async def retry(self, session_id=None):
        if self.proxy and self.proxy.session_id == session_id:
            self.proxy.retry_now()
        return {'session_id': session_id}

    async def disconnect(self):
        if self.proxy:
            await self.proxy.stop()
            self.proxy = None
        return {}

    async def check(self, session_id=None):
        if not self.proxy:
            raise BridgeError('Сначала включите локальный прокси.')
        proxy = self.proxy
        if session_id and proxy.session_id != session_id:
            return {'discarded': True, 'session_id': session_id}
        started = time.perf_counter()
        latency_error = None
        try:
            _, writer = await proxy.upstream('claude.ai', 443)
            latency = round((time.perf_counter() - started) * 1000)
            await close_writer(writer)
        except Exception as error:
            latency, latency_error = None, explain_error(error)
        result = await asyncio.to_thread(collect_statistics, proxy.port, proxy.context)
        if self.proxy is not proxy:
            return {'discarded': True, 'session_id': proxy.session_id}
        if latency_error:
            result['errors']['connect_latency'] = latency_error
        return result | {'connect_ms': latency, 'session_id': proxy.session_id,
                         'server': {'host': proxy.endpoint.host, 'port': proxy.endpoint.port,
                                    'country': proxy.endpoint.country, 'city': proxy.endpoint.city}}

    async def quota(self):
        if not self.account:
            raise BridgeError('Сначала загрузите профиль Firefox.')
        await asyncio.to_thread(self.account.request, 'token', 'HEAD')
        return self.account.quota

    def stats(self):
        proxy = self.proxy
        return {'active': proxy.active, 'up': proxy.up, 'down': proxy.down,
                'uptime': int(time.monotonic() - proxy.since),
                'health': proxy.health_info()} if proxy else None

    def shutdown(self):
        if self.loop.is_running():
            async def cleanup():
                await self.disconnect()
                tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                await self.loop.shutdown_asyncgens()
                await self.loop.shutdown_default_executor(timeout=15)
            future = asyncio.run_coroutine_threadsafe(cleanup(), self.loop)
            future.result(timeout=20)
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=3)
            self.loop.close()
