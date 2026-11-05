"""Read-only Net.Coffee diagnostics through the explicitly supplied local proxy."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import http.client
import ipaddress
import json
import ssl
import time
import urllib.parse

# Matches the rule in /claude/claude-page.js?v=20260916a, reviewed 2026-11-03.
# This is Net.Coffee's classification, not an authoritative Anthropic policy.
NETCOFFEE_RESTRICTED = {'CN', 'HK', 'MO', 'RU', 'KP', 'IR', 'SY', 'CU', 'BY', 'VE'}
MAX_BODY = 512 * 1024


class NetCoffeeError(Exception):
    def __init__(self, message, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


def proxy_get(port, context, host, path):
    if host not in ('claude.ai', 'ip.net.coffee'):
        raise ValueError('Unsupported diagnostic host')
    connection = http.client.HTTPSConnection('127.0.0.1', port, context=context, timeout=12)
    connection.set_tunnel(host, 443)
    try:
        connection.request('GET', path, headers={
            'Host': host, 'Connection': 'close', 'User-Agent': 'FoxBridge/0.6',
            'Accept': 'application/json, text/plain'})
        response = connection.getresponse()
        if response.status != 200:
            wait = response.getheader('Retry-After', '')
            wait = min(int(wait), 3600) if wait.isdigit() else (60 if response.status == 429 else 0)
            raise NetCoffeeError(f'{host}: HTTP {response.status}.', wait)
        body = response.read(MAX_BODY + 1)
        if len(body) > MAX_BODY:
            raise NetCoffeeError(f'{host}: слишком большой ответ.')
        return body
    except NetCoffeeError:
        raise
    except ssl.SSLCertVerificationError:
        raise NetCoffeeError(f'{host}: сертификат TLS не прошёл проверку.')
    except TimeoutError:
        raise NetCoffeeError(f'{host}: время ожидания истекло.')
    except (OSError, http.client.HTTPException):
        raise NetCoffeeError(f'{host}: не удалось получить ответ через прокси.')
    finally:
        connection.close()


def score_from_risk(risk):
    raw = risk.get('trust_score')
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not 0 <= raw <= 100:
        raw = None
    code = str(risk.get('countryCode') or '').upper()
    restricted = code in NETCOFFEE_RESTRICTED
    score = 0 if restricted else raw
    return score, raw, restricted


def collect_statistics(port, context, getter=None):
    get = getter or (lambda host, path: proxy_get(port, context, host, path))
    started = time.perf_counter()
    trace_body = get('claude.ai', '/cdn-cgi/trace').decode('utf-8', errors='replace')
    trace = dict(line.split('=', 1) for line in trace_body.splitlines() if '=' in line)
    try:
        address = ipaddress.ip_address(trace.get('ip', '').strip())
        if not address.is_global:
            raise ValueError('Non-public address')
    except ValueError:
        raise NetCoffeeError('Claude trace не вернул корректный публичный IP. Статистика не обновлена.')
    ip = str(address)
    encoded = urllib.parse.quote(ip, safe=':')
    errors, data, retry_after = {}, {}, 0

    def fetch(kind):
        try:
            parsed = json.loads(get('ip.net.coffee', f'/api/{kind}/{encoded}'))
            if not isinstance(parsed, dict) or parsed.get('error') or parsed.get('success') is False:
                raise NetCoffeeError(f'Net.Coffee {kind}: некорректный ответ.')
            if kind == 'iprisk':
                try:
                    representative = ipaddress.ip_address(parsed.get('ip', ''))
                    if representative != address:
                        # Net.Coffee caches risk per CIDR; its `ip` can be the
                        # representative that first populated that subnet cache.
                        network = ipaddress.ip_network(parsed.get('cidr', ''), strict=False)
                        if address not in network or representative not in network:
                            raise ValueError('Mismatched IP and CIDR')
                except ValueError:
                    raise NetCoffeeError('Net.Coffee вернул оценку для другого IP. Результат отклонён.')
            return kind, parsed, None
        except (json.JSONDecodeError, UnicodeError):
            return kind, None, NetCoffeeError(f'Net.Coffee {kind}: вместо JSON получен другой ответ.')
        except NetCoffeeError as error:
            return kind, None, error

    with ThreadPoolExecutor(max_workers=2) as pool:
        for kind, parsed, error in pool.map(fetch, ('geoip', 'iprisk')):
            if error:
                errors[kind] = str(error)
                retry_after = max(retry_after, error.retry_after)
            else:
                data[kind] = parsed

    risk = data.get('iprisk', {})
    score, raw_score, restricted = score_from_risk(risk)
    if score is None and 'iprisk' not in errors:
        errors['iprisk'] = 'Net.Coffee не вернул корректный trust_score.'
    return {
        'provider': 'Net.Coffee', 'checked_at': datetime.now(timezone.utc).isoformat(),
        'ip': ip, 'ip_version': address.version, 'trace': trace,
        'geoip': data.get('geoip'), 'iprisk': data.get('iprisk'),
        'score': score, 'raw_score': raw_score, 'region_adjusted': restricted,
        'score_scope': ('ip' if ipaddress.ip_address(risk['ip']) == address else 'cidr') if risk else None,
        'errors': errors, 'retry_after': retry_after,
        'request_ms': round((time.perf_counter() - started) * 1000),
    }
