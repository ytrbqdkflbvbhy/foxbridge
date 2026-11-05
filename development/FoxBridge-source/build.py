"""Build from this folder with Python + requirements.txt + PyInstaller installed."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

base = Path(__file__).resolve().parent
system = Path(os.environ.get('WINDIR', 'C:/Windows'))
env = os.environ.copy()
# Avoid collecting unrelated Qt/ICU DLLs from developer tools on the user's PATH.
env['PATH'] = os.pathsep.join(map(str, [system / 'System32', system, Path(sys.executable).parent]))
subprocess.run([
    sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--windowed',
    '--name', 'FoxBridge', '--icon', str(base / 'icon.ico'),
    '--add-data', str(base / 'cacert.pem') + ';.',
    '--distpath', str(base / 'dist'), '--workpath', str(base / 'build'),
    '--specpath', str(base / 'build'), str(base / 'main.py'),
], env=env, check=True)
destination = base / 'dist/FoxBridge'
# Qt on Windows uses the OS ICU API; a developer installation may have a
# different DLL with the same name. Let Windows resolve its own ICU libraries.
for name in ('icuuc.dll', 'icuin.dll', 'icudt.dll'):
    candidate = destination / '_internal' / name
    if candidate.exists():
        candidate.unlink()
for name in ('README.md', 'THIRD-PARTY-NOTICES.md'):
    shutil.copy2(base / name, destination / name)
if (base / 'licenses').is_dir():
    shutil.copytree(base / 'licenses', destination / 'licenses', dirs_exist_ok=True)
print(destination / 'FoxBridge.exe')
