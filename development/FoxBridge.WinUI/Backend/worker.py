"""Private stdio adapter for the existing engine. No TCP control port or Qt."""
import dataclasses
import json
import sys
import threading
import time

from bridge import Engine, BridgeError, explain_error, firefox_profiles
from launcher import powershell_bootstrap
from timezones import COUNTRY_TIMEZONES

sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
lock = threading.Lock()


def emit(kind, data):
    if kind == 'load':
        data = dict(data)
        data['servers'] = [dataclasses.asdict(s) | {'label': s.label} for s in data['servers']]
        data.pop('email', None)
    if kind == 'check' and not data.get('discarded'):
        risk = data.get('iprisk') or {}
        supplied = risk.get('timezone')
        data = data | {'timezone_candidate': supplied or COUNTRY_TIMEZONES.get(str(risk.get('countryCode', '')).upper()),
                       'timezone_estimated': not bool(supplied)}
    with lock:
        print(json.dumps({'kind': kind, 'data': data}, ensure_ascii=False), flush=True)


def main():
    engine = Engine(emit)
    check_until = 0
    try:
        emit('ready', {})
        for line in sys.stdin:
            if len(line) > 16384:
                raise BridgeError('Слишком большая команда интерфейса.')
            request = json.loads(line)
            operation = request.get('operation')
            args = request.get('args') or {}
            try:
                if operation == 'shutdown':
                    break
                if operation == 'profiles':
                    if engine.proxy:
                        raise BridgeError('Сначала отключите прокси.')
                    emit('profiles', [{'name': n, 'path': str(p), 'preferred': d}
                                      for n, p, d in firefox_profiles(args.get('profiles_ini'))])
                elif operation == 'activity':
                    emit('activity', engine.stats())
                elif operation == 'commands':
                    if not engine.proxy or engine.proxy.health != 'online':
                        raise BridgeError('Прокси не готов.')
                    emit('commands', {'text': powershell_bootstrap(engine.proxy.port),
                                      'session_id': engine.proxy.session_id})
                elif operation in ('load', 'connect', 'disconnect', 'retry', 'quota', 'check'):
                    if operation == 'check':
                        if time.monotonic() < check_until:
                            raise BridgeError('Подождите перед повторной проверкой статистики.')
                        check_until = time.monotonic() + 10
                    engine.submit(operation, **args).result()
                else:
                    raise BridgeError('Неизвестная команда интерфейса.')
            except Exception as error:
                emit('error', {'operation': operation, 'message': explain_error(error),
                               'session_id': args.get('session_id')})
    finally:
        engine.shutdown()


if __name__ == '__main__':
    main()
