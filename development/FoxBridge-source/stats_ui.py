"""Detailed IP diagnostics. Provider text is always displayed as plain text."""
from datetime import datetime
import json

from PySide6.QtCore import Qt, Signal, QTimeZone
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
)


def time_text(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone().strftime('%d.%m.%Y %H:%M:%S UTC%z')
    except (AttributeError, ValueError):
        return '—'


def value_text(value):
    if value is None or value == '':
        return 'Нет данных'
    if isinstance(value, bool):
        return 'Да' if value else 'Нет'
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    text = str(value)
    return text if len(text) <= 2000 else text[:2000] + '… (полностью — во вкладке JSON)'


def table():
    widget = QTableWidget(0, 2)
    widget.setHorizontalHeaderLabels(['Параметр', 'Значение'])
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setWordWrap(True)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    widget.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    return widget


def fill(widget, pairs):
    widget.setRowCount(len(pairs))
    for row, (name, value) in enumerate(pairs):
        widget.setItem(row, 0, QTableWidgetItem(name))
        widget.setItem(row, 1, QTableWidgetItem(value_text(value)))
    widget.resizeRowsToContents()


class StatisticsDialog(QDialog):
    refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('FoxBridge — подробная статистика IP')
        self.resize(940, 720)
        self.setMinimumSize(700, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        row = QHBoxLayout()
        title = QLabel('Статистика Net.Coffee')
        title.setObjectName('heading')
        row.addWidget(title)
        row.addStretch()
        self.refresh = QPushButton('Обновить статистику')
        self.refresh.clicked.connect(self.refresh_requested.emit)
        row.addWidget(self.refresh)
        layout.addLayout(row)
        self.state = QLabel('Данные ещё не получены.')
        self.state.setTextFormat(Qt.TextFormat.PlainText)
        self.state.setWordWrap(True)
        layout.addWidget(self.state)
        note = QLabel('Оценка стороннего сервиса, не рейтинг Anthropic. Время ниже — время запроса; '
                      'данные в базе сервиса могут быть кэшированы. География разных источников может отличаться.')
        note.setObjectName('muted')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tabs = QTabWidget()
        self.network = table()
        self.risk = table()
        self.geography = table()
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.tabs.addTab(self.network, 'IP и сеть')
        self.tabs.addTab(self.risk, 'Риски и score')
        self.tabs.addTab(self.geography, 'География')
        self.tabs.addTab(self.raw, 'Полный ответ JSON')
        layout.addWidget(self.tabs, 1)
        self.activity = QLabel('Нет активного подключения.')
        self.activity.setObjectName('muted')
        self.activity.setTextFormat(Qt.TextFormat.PlainText)
        self.activity.setWordWrap(True)
        layout.addWidget(self.activity)
        close = QPushButton('Закрыть')
        close.clicked.connect(self.close)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

    def set_snapshot(self, snapshot, attempt, error, stale, connected, busy,
                     cooldown=0, live_stats=None):
        self.update_activity(live_stats, connected, busy, cooldown)
        snapshot = snapshot or {}
        risk = snapshot.get('iprisk') or {}
        geo = snapshot.get('geoip') or {}
        trace = snapshot.get('trace') or {}
        server = snapshot.get('server') or {}
        if busy:
            state = 'Обновление в процессе. Сохранённый результат ниже не заменяется до получения нового score.'
        elif not connected:
            state = 'Прокси выключен. Ниже — сохранённый результат предыдущей проверки.'
        elif error:
            state = 'Обновление не завершено: ' + error
            if snapshot.get('score') is not None:
                state += '\nНиже сохранённый результат; он отмечен как устаревший.'
            elif snapshot:
                state += '\nНиже частичные данные последней попытки. Score недоступен.'
        elif snapshot:
            state = 'Получено: ' + time_text(snapshot.get('checked_at'))
            if snapshot.get('errors'):
                state += ' · часть сведений недоступна'
        else:
            state = 'Данные ещё не получены.'
        self.state.setText(state)
        rows = [
            ('Источник', 'Net.Coffee; IP определяется через claude.ai/cdn-cgi/trace'),
            ('Запрос выполнен', time_text(snapshot.get('checked_at'))),
            ('Состояние результата', 'Устаревший / сохранённый' if stale else 'Получен в текущем подключении'),
            ('Выходной IP для claude.ai', snapshot.get('ip')),
            ('Версия IP', f'IPv{snapshot["ip_version"]}' if snapshot.get('ip_version') else None),
            ('Страна по Claude trace', trace.get('loc')),
            ('Узел Cloudflare', trace.get('colo')),
            ('Выбранная страна сервера', server.get('country')),
            ('Прокси Firefox', server.get('host')),
            ('Порт удалённого прокси', server.get('port')),
            ('ASN', risk.get('asn')),
            ('Оператор ASN', risk.get('asOrganization')),
            ('Название ASN', risk.get('asname')),
            ('Тип ASN', risk.get('asn_kind')),
            ('Провайдер / ISP', geo.get('isp')),
            ('Компания', risk.get('company_name')),
            ('Тип компании', risk.get('company_type')),
            ('CIDR', risk.get('cidr')),
            ('Reverse DNS', risk.get('rdns')),
            ('RPKI', risk.get('rpki_status')),
            ('CONNECT до claude.ai, мс', snapshot.get('connect_ms')),
            ('Вся проверка Net.Coffee, мс', snapshot.get('request_ms')),
            ('Срок выдачи ASN', risk.get('asn_allocated')),
            ('IPv4-адресов ASN', risk.get('asn_ipv4_count')),
            ('Пропускная способность ASN (по базе)', risk.get('asn_tbps')),
        ]
        for name, message in snapshot.get('errors', {}).items():
            rows.append((f'Ошибка источника: {name}', message))
        fill(self.network, rows)
        verdict = risk.get('ai_verdict') or {}
        intelligence = risk.get('intelligence') or {}
        verdict = verdict if isinstance(verdict, dict) else {}
        intelligence = intelligence if isinstance(intelligence, dict) else {}
        rows = [
            ('Итоговый score страницы Net.Coffee', snapshot.get('score')),
            ('Исходный trust_score API', snapshot.get('raw_score')),
            ('Область оценки', 'Подсеть CIDR (кэш Net.Coffee)' if snapshot.get('score_scope') == 'cidr' else
                               'Совпадающий IP' if snapshot.get('score_scope') == 'ip' else None),
            ('IP-представитель из ответа базы', risk.get('ip')),
            ('CIDR оценки', risk.get('cidr')),
            ('Корректировка по региональному правилу Net.Coffee', snapshot.get('region_adjusted')),
            ('Страна, используемая правилом сайта', risk.get('countryCode')),
        ]
        for name, key in [
            ('Дата-центр (по базе)', 'is_datacenter'), ('Жилой IP (по базе)', 'isResidential'),
            ('Мобильная сеть (по базе)', 'is_mobile'), ('VPN (по базе)', 'is_vpn'),
            ('Proxy (по базе)', 'is_proxy'), ('Tor (по базе)', 'is_tor'),
            ('Crawler (по базе)', 'is_crawler'), ('Записи о злоупотреблениях', 'is_abuser'),
            ('Bogon', 'is_bogon'), ('Блокировка Reddit (по базе)', 'reddit_blocked'),
        ]:
            rows.append((name, risk.get(key)))
        rows.extend([
            ('Abuser score', risk.get('abuser_score')),
            ('Уровень злоупотреблений', intelligence.get('abuser_level')),
            ('Обнаруженные угрозы (по базе)', intelligence.get('threats')),
            ('Дополнительная репутация', intelligence.get('rep_threat')),
            ('Дата-центр', risk.get('datacenter_name')),
            ('Классификация сервиса', verdict.get('label')),
            ('Уверенность классификации, %', verdict.get('confidence')),
            ('Пояснение сервиса', verdict.get('reasoning')),
            ('VPN trace', risk.get('vpn_trace')),
            ('Связанные домены', risk.get('related_domains')),
            ('Связанные домены ещё обрабатываются', risk.get('related_domains_pending')),
        ])
        fill(self.risk, rows)
        tz = snapshot.get('launch_timezone') or {}
        rows = [('Страна Claude trace', trace.get('loc')),
                ('Часовой пояс Windows (не изменяется)', QTimeZone.systemTimeZoneId().data().decode()),
                ('TZ для новых терминалов', tz.get('id')),
                ('Текущее смещение выбранного пояса', tz.get('utc')),
                ('Источник выбранного пояса', 'Поле timezone API Net.Coffee' if tz.get('source') == 'iprisk' else
                  'Оценка по стране, как на странице Net.Coffee' if tz.get('source') else None),
                ('Пояс определён приблизительно', tz.get('estimated')),
                ('Ошибка определения пояса', tz.get('error')),
                ('Область действия', 'Переменная окружения TZ новых процессов; не подмена Windows API и не настройка Firefox')]
        for prefix, data in [('GeoIP API', geo), ('Risk API', risk)]:
            for name, key in [('Страна', 'country'), ('Регион', 'region'), ('Город', 'city')]:
                rows.append((f'{prefix}: {name}', data.get(key)))
        rows.extend([('Страна регистрации сети', risk.get('registered_country')),
                     ('Код страны регистрации', risk.get('registered_country_code')),
                     ('Диапазон сети', risk.get('range'))])
        for index, source in enumerate(risk.get('geo_sources') or []):
            if isinstance(source, dict):
                rows.append((f'Источник географии {source.get("src", index + 1)}', source))
        fill(self.geography, rows)
        self.raw.setPlainText(json.dumps(
            {'displayed_result': snapshot or None, 'last_attempt': attempt,
             'last_error': error or None, 'stale': stale}, ensure_ascii=False, indent=2))

    def update_activity(self, stats, connected, busy, cooldown):
        self.refresh.setEnabled(connected and not busy and cooldown == 0)
        self.refresh.setText('Обновляем…' if busy else
                             f'Обновить через {cooldown} с' if connected and cooldown else 'Обновить статистику')
        if stats:
            seconds = stats['uptime']
            self.activity.setText(
                f'Сессия {seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d} · '
                f'Соединений: {stats["active"]} · ↑ {stats["up"] / 1e6:.2f} МБ · ↓ {stats["down"] / 1e6:.2f} МБ')
        else:
            self.activity.setText('Нет активного подключения.')
