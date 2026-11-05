"""Net.Coffee-compatible timezone selection. No changes to the Windows timezone."""
import re


# Same country fallback as Net.Coffee /claude/claude-page.js?v=20260916a.
# Multi-zone countries are estimates, not an IP-specific measurement.
COUNTRY_TIMEZONES = dict(zip(
    'CN TW HK MO JP KR SG MY TH VN ID PH IN PK BD IR IL AE SA TR RU UA GB IE FR DE IT ES PT NL '
    'BE CH AT SE NO DK FI PL CZ GR RO US CA MX BR AR CL AU NZ ZA EG NG KE'.split(),
    ('Asia/Shanghai Asia/Taipei Asia/Hong_Kong Asia/Macau Asia/Tokyo Asia/Seoul Asia/Singapore '
     'Asia/Kuala_Lumpur Asia/Bangkok Asia/Ho_Chi_Minh Asia/Jakarta Asia/Manila Asia/Kolkata '
     'Asia/Karachi Asia/Dhaka Asia/Tehran Asia/Jerusalem Asia/Dubai Asia/Riyadh Europe/Istanbul '
     'Europe/Moscow Europe/Kyiv Europe/London Europe/Dublin Europe/Paris Europe/Berlin Europe/Rome '
     'Europe/Madrid Europe/Lisbon Europe/Amsterdam Europe/Brussels Europe/Zurich Europe/Vienna '
     'Europe/Stockholm Europe/Oslo Europe/Copenhagen Europe/Helsinki Europe/Warsaw Europe/Prague '
     'Europe/Athens Europe/Bucharest America/Los_Angeles America/Toronto America/Mexico_City '
     'America/Sao_Paulo America/Argentina/Buenos_Aires America/Santiago Australia/Sydney '
     'Pacific/Auckland Africa/Johannesburg Africa/Cairo Africa/Lagos Africa/Nairobi').split()))


def validate_timezone(value):
    # No paths, shell quotes, POSIX expressions or embedded control characters.
    if (not isinstance(value, str) or len(value) > 100 or
            not re.fullmatch(r'[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+)*', value)):
        raise ValueError('Некорректный идентификатор часового пояса.')
    from PySide6.QtCore import QTimeZone
    if not QTimeZone.isTimeZoneIdAvailable(value.encode('ascii')):
        raise ValueError('Часовой пояс не поддерживается локальной базой Qt.')
    return value


def select_timezone(snapshot):
    risk = (snapshot or {}).get('iprisk') or {}
    supplied = risk.get('timezone')
    country = str(risk.get('countryCode') or '').upper()
    if supplied:
        zone, source = supplied, 'iprisk'
    else:
        zone, source = COUNTRY_TIMEZONES.get(country), 'country_estimate'
    if not zone:
        return {'id': None, 'error': 'Net.Coffee не вернул пояс или известную страну; запуск терминала недоступен.'}
    try:
        validate_timezone(zone)
    except ValueError as error:
        return {'id': None, 'error': str(error)}
    from PySide6.QtCore import QDateTime, QTimeZone
    offset = QDateTime.currentDateTimeUtc().toTimeZone(QTimeZone(zone.encode('ascii'))).offsetFromUtc() // 60
    absolute = abs(offset)
    return {'id': zone, 'source': source, 'country': country, 'offset_minutes': offset,
            'utc': f'UTC{"+" if offset >= 0 else "-"}{absolute // 60:02d}:{absolute % 60:02d}',
            'estimated': source == 'country_estimate', 'error': None}
