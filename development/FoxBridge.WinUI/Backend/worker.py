"""Private stdio adapter for the existing engine. No TCP control port or Qt."""
import dataclasses
import asyncio
import ipaddress
import json
import math
import sys
import threading
import time

from bridge import Engine, BridgeError, explain_error, firefox_profiles
from launcher import powershell_bootstrap
from timezones import COUNTRY_TIMEZONES
from tun import TunController, enable_worker_bypass, disable_worker_bypass, is_admin

sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
lock = threading.Lock()


async def check_before_tun(engine, session):
    result = await engine.check(session_id=session)
    score = result.get('score')
    try:
        ipaddress.ip_address(result.get('ip', ''))
        valid_ip = True
    except ValueError:
        valid_ip = False
    if (result.get('discarded') or result.get('session_id') != session or not valid_ip
            or type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 100
            or not engine.proxy or engine.proxy.session_id != session or engine.proxy.health != 'online'):
        raise BridgeError('Проверка IP и оценки Net.Coffee не завершилась успешно. TUN не включён. Повторите подключение.')
    return result


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
    tunnel = None
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
                elif operation == 'connect':
                    use_tun = args.get('tun', False)
                    if type(use_tun) is not bool:
                        raise BridgeError('Некорректный режим подключения.')
                    if engine.proxy or tunnel:
                        raise BridgeError('Сначала отключите текущее подключение.')
                    if use_tun:
                        if not is_admin():
                            raise BridgeError('Для TUN запустите FoxBridge от имени администратора.')
                        index = args.get('server_index')
                        if type(index) is not int or not 0 <= index < len(engine.servers):
                            raise BridgeError('Выберите доступный сервер.')
                        enable_worker_bypass(('vpn.mozilla.org', engine.servers[index].host))
                    try:
                        result = asyncio.run_coroutine_threadsafe(engine.connect(
                            args['server_index'], args['port']), engine.loop).result()
                        checked = None
                        if use_tun:
                            emit('connect_progress', {'message': 'Проверяем IP и Net.Coffee перед TUN…'})
                            check_until = time.monotonic() + 10
                            checked = asyncio.run_coroutine_threadsafe(
                                check_before_tun(engine, result['session_id']), engine.loop).result()
                            emit('connect_progress', {'message': 'Проверка пройдена. Включаем TUN…'})
                            tunnel = TunController(lambda: emit('tun_lost', {}))
                            tunnel.start(args['port'])
                        emit('connect', result | {'tun': use_tun, 'prechecked': checked is not None})
                        if checked is not None:
                            emit('check', checked)
                        if tunnel:
                            tunnel.arm()
                    except Exception:
                        if tunnel:
                            tunnel.stop()
                            tunnel = None
                        asyncio.run_coroutine_threadsafe(engine.disconnect(), engine.loop).result()
                        disable_worker_bypass()
                        raise
                elif operation in ('load', 'disconnect', 'retry', 'quota', 'check'):
                    if operation == 'disconnect':
                        if tunnel:
                            tunnel.stop()
                            tunnel = None
                        disable_worker_bypass()
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
        if tunnel:
            tunnel.stop()
        disable_worker_bypass()
        engine.shutdown()


if __name__ == '__main__':
    main()
