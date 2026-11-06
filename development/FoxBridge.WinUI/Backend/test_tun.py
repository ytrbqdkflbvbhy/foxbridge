"""Offline TUN checks. Do not modify routes, DNS, adapters or Firefox accounts."""
import json
import os
import queue
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'FoxBridge-source'))
from bridge import BridgeError
import tun


class TunTests(unittest.TestCase):
    def test_no_direct_fallback_udp_blocked_dns_proxied_and_dual_stack(self):
        config = tun.configuration(12345)
        self.assertEqual(config['route']['final'], 'foxbridge')
        self.assertEqual([o['type'] for o in config['outbounds']], ['http'])
        self.assertEqual(config['outbounds'][0]['server_port'], 12345)
        rules = config['route']['rules']
        self.assertEqual(rules[0], {'port': 53, 'action': 'hijack-dns'})
        self.assertEqual(rules[1], {'network': 'udp', 'action': 'reject'})
        self.assertEqual(config['dns']['servers'][0]['detour'], 'foxbridge')
        inbound = config['inbounds'][0]
        self.assertTrue(inbound['strict_route'])
        self.assertTrue(any(':' in a for a in inbound['address']))

    def test_invalid_port_rejected(self):
        for value in (True, '8765', 1, 65536, 8765.5):
            with self.assertRaises(ValueError):
                tun.configuration(value)

    def test_unprivileged_start_cannot_spawn_engine(self):
        with patch.object(tun, 'is_admin', return_value=False), patch.object(tun.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(BridgeError, 'администратора'):
                tun.TunController(lambda: None).start(8765)
            spawn.assert_not_called()

    def test_only_worker_socket_factory_is_replaced_and_restored(self):
        original = socket.socket
        with patch.object(tun, 'capture_interfaces', return_value={socket.AF_INET: 12}):
            try:
                tun.enable_worker_bypass()
                self.assertIs(socket.socket, tun.WorkerSocket)
                with socket.socket() as local:
                    # Loopback never gets a physical-interface bind.
                    local._outside_tun(('127.0.0.1', 8765))
                    self.assertEqual(local.getsockopt(socket.IPPROTO_IP, 31), 0)
            finally:
                tun.disable_worker_bypass()
        self.assertIs(socket.socket, original)

    def test_stop_is_idempotent(self):
        controller = tun.TunController(lambda: None)
        controller.stop()
        controller.stop()

    def test_token_and_proxy_bootstrap_dns_is_resolved_before_tun(self):
        original = tun._original_getaddrinfo
        calls = []
        def resolver(host, *args, **kwargs):
            calls.append(host)
            return original('127.0.0.2' if host == 'vpn.invalid' else host, *args, **kwargs)
        with patch.object(tun, 'capture_interfaces', return_value={socket.AF_INET: 12}), \
             patch.object(tun, '_original_getaddrinfo', side_effect=resolver):
            try:
                tun.enable_worker_bypass(('vpn.invalid',))
                calls.clear()
                values = socket.getaddrinfo('vpn.invalid', 443, type=socket.SOCK_STREAM)
                self.assertEqual(values[0][4][0], '127.0.0.2')
                self.assertEqual(calls, ['127.0.0.2'])
            finally:
                tun.disable_worker_bypass()
        tun.disable_worker_bypass()

    def test_configuration_accepted_by_pinned_engine(self):
        engine = Path(__file__).resolve().parents[1] / 'Tools' / 'sing-box.exe'
        if not engine.exists():
            self.skipTest('Install-TunEngine.ps1 must run to validate binary configuration')
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.json'
            config.write_text(json.dumps(tun.configuration(8765)), encoding='utf-8')
            result = subprocess.run([str(engine), 'check', '-c', str(config)],
                                    capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))

    @unittest.skipUnless(os.name == 'nt', 'Windows console lifecycle')
    def test_supervisor_eof_stops_engine_without_network_changes(self):
        base = Path(__file__).resolve().parent
        engine = base.parent / 'Tools' / 'sing-box.exe'
        if not engine.exists():
            self.skipTest('TUN engine not installed')
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'empty.json'
            config.write_text('{}', encoding='utf-8')  # No adapters, routes or listeners.
            startup = subprocess.STARTUPINFO()
            startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            process = subprocess.Popen([sys.executable, '-I', '-u', str(base / 'tun_host.py'),
                                        str(engine), str(config)], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, startupinfo=startup,
                                       creationflags=subprocess.CREATE_NEW_CONSOLE)
            lines = queue.Queue()
            threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
            try:
                self.assertEqual(lines.get(timeout=10).strip(), 'ready')
                _, error = process.communicate(input='', timeout=12)
                self.assertEqual(process.returncode, 0, error)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
