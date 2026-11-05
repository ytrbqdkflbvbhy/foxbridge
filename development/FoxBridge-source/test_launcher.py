import base64
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys
import unittest

from launcher import powershell_arguments, powershell_bootstrap, proxy_environment


class LauncherTests(unittest.TestCase):
    def test_proxy_environment_replaces_conflicting_values(self):
        result = proxy_environment(18765, {'https_proxy': 'old', 'ALL_PROXY': 'old', 'PATH': 'keep'})
        self.assertNotIn('https_proxy', result)
        self.assertNotIn('ALL_PROXY', result)
        self.assertEqual(result['HTTPS_PROXY'], 'http://127.0.0.1:18765')
        self.assertEqual(result['PATH'], 'keep')

    def test_terminal_arguments_encode_the_same_bootstrap(self):
        args = powershell_arguments(18765)
        self.assertEqual(base64.b64decode(args[-1]).decode('utf-16-le'), powershell_bootstrap(18765))

    @unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows PowerShell required')
    def test_codex_gets_isolated_mode_but_management_commands_do_not(self):
        with tempfile.TemporaryDirectory(prefix='foxbridge-launcher-') as temporary:
            root = Path(temporary)
            (root / 'codex.cmd').write_text(
                '@echo off\nif "%~1"=="--help" (\n echo --no-daemon\n exit /b 0\n)\n'
                'echo FAKE_CODEX_ARGS:%*\n', encoding='ascii')
            bootstrap = powershell_bootstrap(18765)
            script = bootstrap + '\ncodex\ncodex resume session-one\ncodex exec task\ncodex review\ncodex agents\ncodex --remote ws://localhost:1234\n'
            encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'])
            run = subprocess.run(['powershell.exe', '-NoProfile', '-EncodedCommand', encoded],
                                 env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('FAKE_CODEX_ARGS:--no-daemon', run.stdout)
            self.assertIn('FAKE_CODEX_ARGS:--no-daemon resume session-one', run.stdout)
            self.assertIn('FAKE_CODEX_ARGS:--no-daemon exec task', run.stdout)
            self.assertIn('FAKE_CODEX_ARGS:--no-daemon review', run.stdout)
            self.assertIn('FAKE_CODEX_ARGS:agents', run.stdout)
            self.assertIn('FAKE_CODEX_ARGS:--remote ws://localhost:1234', run.stdout)
            self.assertNotIn('FAKE_CODEX_ARGS:--no-daemon agents', run.stdout)

    @unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows PowerShell required')
    def test_old_codex_is_not_given_an_unsupported_flag(self):
        with tempfile.TemporaryDirectory(prefix='foxbridge-launcher-') as temporary:
            root = Path(temporary)
            (root / 'codex.cmd').write_text(
                '@echo off\nif "%~1"=="--help" (\n echo legacy-version\n exit /b 0\n)\n'
                'echo LEGACY_CODEX_ARGS:%*\n', encoding='ascii')
            script = powershell_bootstrap(18765) + '\ncodex resume session-one\n'
            encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'])
            run = subprocess.run(['powershell.exe', '-NoProfile', '-EncodedCommand', encoded],
                                 env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('LEGACY_CODEX_ARGS:resume session-one', run.stdout)
            self.assertNotIn('LEGACY_CODEX_ARGS:--no-daemon', run.stdout)

    @unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows PowerShell required')
    def test_child_and_grandchild_inherit_proxy_without_touching_parent_environment(self):
        with tempfile.TemporaryDirectory(prefix='foxbridge-inheritance-') as temporary:
            root = Path(temporary)
            (root / 'codex.cmd').write_text('@echo off\necho legacy-version\n', encoding='ascii')
            child = root / 'child.py'
            child.write_text('import os, subprocess, sys, json\n'
                'print("CHILD_PROXY:" + os.environ.get("HTTPS_PROXY", "missing"), flush=True)\n'
                'subprocess.run([sys.executable, "-c", '
                '"import os; print(\\\"GRANDCHILD_PROXY:\\\" + os.environ.get(\\\"HTTPS_PROXY\\\", \\\"missing\\\"))"], check=True)\n', encoding='utf-8')
            # Only the new shell receives the bootstrap. Its native children inherit it.
            script = powershell_bootstrap(18765) + "\n& '" + sys.executable.replace("'", "''") + "' '" + str(child).replace("'", "''") + "'\n"
            encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'], HTTPS_PROXY='http://old.invalid:1')
            run = subprocess.run(['powershell.exe', '-NoProfile', '-EncodedCommand', encoded], env=env,
                                 capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('CHILD_PROXY:http://127.0.0.1:18765', run.stdout)
            self.assertIn('GRANDCHILD_PROXY:http://127.0.0.1:18765', run.stdout)
            self.assertEqual(env['HTTPS_PROXY'], 'http://old.invalid:1')


if __name__ == '__main__':
    unittest.main()
