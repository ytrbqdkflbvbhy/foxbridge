"""Windows TUN lifecycle. Only the network worker binds its sockets outside TUN."""
import asyncio
import contextlib
import ctypes
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import queue
import socket
import struct
import subprocess
import sys
import tempfile
import threading
from bridge import BridgeError
import bridge

ENGINE_SHA256 = '8f30682ee198855440d2a7fe070d5d3653793b2d01bb8c85eea88f93bbe7c8b9'


def is_admin():
    return os.name == 'nt' and bool(ctypes.windll.shell32.IsUserAnAdmin())


def configuration(port):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Invalid proxy port')
    return {
        'log': {'level': 'info', 'timestamp': False},
        'dns': {'servers': [{'type': 'https', 'tag': 'remote', 'server': '1.1.1.1',
                             'server_port': 443, 'path': '/dns-query', 'detour': 'foxbridge'}],
                'final': 'remote'},
        'inbounds': [{'type': 'tun', 'tag': 'tun', 'interface_name': 'FoxBridge-TUN',
                      'address': ['172.29.255.1/30', 'fdfe:dcba:fb00::1/126'],
                      'mtu': 1500, 'auto_route': True, 'strict_route': True,
                      'dns_mode': 'hijack'}],
        'outbounds': [{'type': 'http', 'tag': 'foxbridge', 'server': '127.0.0.1',
                       'server_port': port}],
        'route': {'auto_detect_interface': True, 'final': 'foxbridge', 'rules': [
            {'port': 53, 'action': 'hijack-dns'},
            {'network': 'udp', 'action': 'reject'},
            {'ip_version': 6, 'protocol': 'icmp', 'action': 'reject'},
            {'protocol': 'icmp', 'action': 'reject'}]}}


_original_socket = socket.socket
_original_getaddrinfo = socket.getaddrinfo
_original_tls_connection = bridge.open_tls_connection
_interfaces = {}
_bootstrap_addresses = {}


def worker_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    addresses = _bootstrap_addresses.get(host.lower() if isinstance(host, str) else host)
    if addresses is None:
        return _original_getaddrinfo(host, port, family, type, proto, flags)
    result = []
    for address in addresses:
        try:
            result.extend(_original_getaddrinfo(address, port, family, type, proto, flags))
        except socket.gaierror:
            continue
    if not result:
        raise socket.gaierror('No bootstrap address for requested family')
    return result


async def bound_tls_connection(host, port, context):
    # Windows Proactor uses ConnectEx, so binding must happen before sock_connect.
    loop = asyncio.get_running_loop()
    addresses = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    error = OSError('Не найден доступный исходный интерфейс для сервера Firefox.')
    for family, kind, protocol, _, address in addresses:
        if family not in _interfaces:
            continue
        sock = WorkerSocket(family, kind, protocol)
        try:
            sock._outside_tun(address)
            sock.setblocking(False)
            await asyncio.wait_for(loop.sock_connect(sock, address), 12)
            return await asyncio.open_connection(sock=sock, ssl=context, server_hostname=host,
                                                  limit=32768, ssl_handshake_timeout=12)
        except OSError as failure:
            error = failure
            sock.close()
        except BaseException:
            sock.close()
            raise
    raise error


def capture_interfaces():
    """Read the physical default routes BEFORE TUN exists, without editing them."""
    interfaces = {}
    for family, address in ((socket.AF_INET, '1.1.1.1'), (socket.AF_INET6, '2606:4700:4700::1111')):
        # SOCKADDR_IN and SOCKADDR_IN6 have little-endian family and network-order address.
        raw = (struct.pack('<H', family) + b'\0\0' + socket.inet_pton(family, address) + b'\0' * 8
               if family == socket.AF_INET else
               struct.pack('<H', family) + b'\0' * 6 + socket.inet_pton(family, address) + b'\0' * 4)
        index = ctypes.c_ulong()
        buffer = ctypes.create_string_buffer(raw)
        if ctypes.windll.iphlpapi.GetBestInterfaceEx(buffer, ctypes.byref(index)) == 0:
            interfaces[family] = index.value
    if socket.AF_INET not in interfaces:
        raise BridgeError('Не найден исходный сетевой интерфейс IPv4.')
    return interfaces


class WorkerSocket(_original_socket):
    def _outside_tun(self, address):
        if self.family not in (socket.AF_INET, socket.AF_INET6) or not _interfaces:
            return
        host = address[0]
        try:
            if ipaddress.ip_address(host.split('%')[0]).is_loopback:
                return
        except ValueError:
            if host.lower() == 'localhost':
                return
        index = _interfaces.get(self.family)
        if index is None:
            raise OSError('Нет исходного интерфейса для этого семейства адресов.')
        level = socket.IPPROTO_IP if self.family == socket.AF_INET else socket.IPPROTO_IPV6
        self.setsockopt(level, 31, struct.pack('!I' if self.family == socket.AF_INET else '=I', index))

    def connect(self, address):
        self._outside_tun(address)
        return super().connect(address)

    def connect_ex(self, address):
        self._outside_tun(address)
        return super().connect_ex(address)


def enable_worker_bypass(hosts=()):
    global _interfaces, _bootstrap_addresses
    # Token refresh and reconnection must not depend on DNS through the very
    # tunnel whose credentials/channel they are trying to restore.
    addresses = {}
    for host in hosts:
        addresses[host.lower()] = list(dict.fromkeys(item[4][0] for item in
            _original_getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    _interfaces = capture_interfaces()
    _bootstrap_addresses = addresses
    socket.socket = WorkerSocket
    socket.getaddrinfo = worker_getaddrinfo
    bridge.open_tls_connection = bound_tls_connection


def disable_worker_bypass():
    global _interfaces, _bootstrap_addresses
    socket.socket = _original_socket
    socket.getaddrinfo = _original_getaddrinfo
    bridge.open_tls_connection = _original_tls_connection
    _interfaces = {}
    _bootstrap_addresses = {}


class TunController:
    def __init__(self, notify):
        self.notify = notify
        self.process = None
        self.folder = None
        self.stopping = False
        self.armed = threading.Event()
        self.exited = threading.Event()
        self.events = queue.Queue()

    def start(self, port):
        if not is_admin():
            raise BridgeError('Для TUN запустите FoxBridge от имени администратора.')
        base = Path(__file__).resolve().parent
        engine = base.parent / 'Tools' / 'sing-box.exe'
        if not engine.is_file():
            raise BridgeError('Не найден TUN-движок. Восстановите папку Tools из полной сборки.')
        if hashlib.sha256(engine.read_bytes()).hexdigest() != ENGINE_SHA256:
            raise BridgeError('TUN-движок повреждён или заменён. Восстановите папку Tools из полной сборки.')
        self.folder = tempfile.TemporaryDirectory(prefix='foxbridge-tun-')
        config = Path(self.folder.name) / 'config.json'
        config.write_text(json.dumps(configuration(port)), encoding='utf-8')
        startup = subprocess.STARTUPINFO()
        startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        self.process = subprocess.Popen(
            [sys.executable, '-I', '-u', str(base / 'tun_host.py'), str(engine), str(config)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', startupinfo=startup,
            creationflags=subprocess.CREATE_NEW_CONSOLE)
        threading.Thread(target=self._read, daemon=True).start()
        try:
            event = self.events.get(timeout=35)
            if event != 'ready':
                raise BridgeError('TUN не запустился. Проверьте наличие прав администратора и конфликтующих VPN.')
            if self.exited.is_set() or self.process.poll() is not None:
                raise BridgeError('TUN завершился во время запуска.')
        except queue.Empty:
            self.stop()
            raise BridgeError('TUN не успел запуститься. Проверьте наличие конфликтующих VPN.') from None
        except BaseException:
            self.stop()
            raise

    def _read(self):
        process = self.process
        for line in process.stdout:
            if line.strip() == 'ready':
                self.events.put('ready')
        self.exited.set()
        self.events.put('stopped')
        self.armed.wait()
        if not self.stopping:
            self.notify()

    def arm(self):
        # Publish 'connect' before a possible asynchronous 'tun_lost' event.
        self.armed.set()

    def stop(self):
        self.stopping = True
        self.armed.set()
        if self.process:
            try:
                if self.process.poll() is None:
                    self.process.stdin.write('stop\n')
                    self.process.stdin.flush()
                    self.process.wait(timeout=12)
            except (OSError, subprocess.TimeoutExpired):
                if self.process.poll() is None:
                    self.process.kill()  # Its job object also owns the engine.
                    self.process.wait(timeout=5)
            finally:
                with contextlib.suppress(OSError):
                    self.process.stdin.close()
                with contextlib.suppress(OSError):
                    self.process.stdout.close()
                self.process = None
        if self.folder:
            self.folder.cleanup()
            self.folder = None
