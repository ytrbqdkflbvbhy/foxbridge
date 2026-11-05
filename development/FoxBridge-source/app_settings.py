"""Portable UI preferences; no account data or credentials are stored here."""
import json
from pathlib import Path
import sys


def settings_path():
    root = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    return root / 'foxbridge-settings.json'


def load_settings(path=None):
    try:
        value = json.loads(Path(path or settings_path()).read_text(encoding='utf-8'))
        filename = value.get('firefox_profiles_ini', '')
        return {'firefox_profiles_ini': filename if isinstance(filename, str) else ''}
    except (OSError, ValueError, AttributeError):
        return {'firefox_profiles_ini': ''}


def save_settings(value, path=None):
    target = Path(path or settings_path())
    payload = {'firefox_profiles_ini': value.get('firefox_profiles_ini', '')}
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(target)
