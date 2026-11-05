from __future__ import annotations

from datetime import datetime, timezone
import math
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QLayout, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QStyle, QTextEdit, QVBoxLayout, QWidget,
)

from bridge import BridgeError, Engine, explain_error, firefox_profiles
from app_settings import load_settings, save_settings
from launcher import powershell_arguments, powershell_bootstrap, proxy_environment
from stats_ui import StatisticsDialog, time_text
from timezones import select_timezone


def app_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor('#ff8a57'))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 18, 18)
    painter.setPen(QColor('#191b24'))
    painter.setFont(QFont('Segoe UI', 27, QFont.Weight.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, 'F')
    painter.end()
    return QIcon(pixmap)


STYLE = '''
QWidget { font-family: "Segoe UI"; font-size: 13px; color: #e7eaf2; }
QMainWindow, QDialog, QWidget#root { background: #11141c; }
QScrollArea { background: #11141c; border: none; }
QLabel#brand { font-size: 28px; font-weight: 700; }
QLabel#muted { color: #929cb1; }
QLabel#heading { font-size: 15px; font-weight: 600; }
QLabel#metric { font-size: 23px; font-weight: 600; }
QLabel#score { font-size: 48px; font-weight: 700; }
QTabWidget::pane { border: 1px solid #354056; border-radius: 7px; }
QTabBar::tab { background: #202b3e; padding: 11px 16px; }
QTabBar::tab:selected { background: #354763; }
QTableWidget, QPlainTextEdit { background: #121722; gridline-color: #2d374b; border: 1px solid #354056; }
QHeaderView::section { background: #263348; padding: 7px; border: none; }
QLabel#status { color: #ffbe89; background: #292433; border-radius: 12px; padding: 7px 14px; }
QFrame#card { background: #1a1f2b; border: 1px solid #2a3243; border-radius: 14px; }
QComboBox, QSpinBox, QLineEdit { background: #121722; border: 1px solid #354056; border-radius: 7px; padding: 9px; min-height: 22px; }
QComboBox:disabled, QSpinBox:disabled { color: #788398; }
QComboBox QAbstractItemView { background: #1a2230; selection-background-color: #384961; }
QPushButton { background: #293449; border: 1px solid #3a4860; border-radius: 8px; padding: 10px 15px; font-weight: 600; }
QPushButton:hover { background: #35455f; }
QPushButton:disabled { color: #6b768b; background: #202735; border-color: #2a3241; }
QPushButton#primary { background: #ff945f; color: #171b24; border: none; padding: 13px; font-size: 15px; }
QPushButton#primary:hover { background: #ffac83; }
QPushButton#primary:disabled { background: #735445; color: #b9a99f; }
QProgressBar { border: none; background: #101620; border-radius: 5px; height: 9px; text-align: center; }
QProgressBar::chunk { background: #8caaff; border-radius: 5px; }
QTextEdit { background: #111722; border: 1px solid #2a3243; border-radius: 8px; padding: 8px; color: #abb8cc; font-family: "Cascadia Mono", "Consolas"; font-size: 11px; }
QToolTip { background: #293449; color: #eef2fa; border: 1px solid #435777; }
'''


class Events(QObject):
    message = Signal(str, object)


def card():
    frame = QFrame()
    frame.setObjectName('card')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(9)
    layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
    return frame, layout


def label(text, role=None):
    item = QLabel(text)
    item.setTextFormat(Qt.TextFormat.PlainText)
    if role:
        item.setObjectName(role)
    return item


class SettingsDialog(QDialog):
    def __init__(self, preferences, parent=None, save_path=None):
        super().__init__(parent)
        self.preferences = dict(preferences)
        self.save_path = save_path
        self.setWindowTitle('Настройки FoxBridge')
        self.setMinimumWidth(760)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)
        layout.addWidget(label('Расположение профилей Firefox', 'heading'))
        layout.addWidget(label('Если автоматический поиск не сработал, укажите файл вручную.', 'muted'))
        row = QHBoxLayout()
        row.addWidget(label('Файл profiles.ini'))
        self.path_edit = QLineEdit(self.preferences.get('firefox_profiles_ini', ''))
        self.path_edit.setPlaceholderText('Автоматически: %APPDATA%\\Mozilla\\Firefox\\profiles.ini')
        self.path_edit.setClearButtonEnabled(True)
        self.path_edit.setToolTip('Нужен файл profiles.ini — он содержит список и пути профилей Firefox.')
        row.addWidget(self.path_edit, 1)
        browse = QPushButton('Обзор…')
        browse.clicked.connect(self.browse)
        row.addWidget(browse)
        layout.addLayout(row)
        hint = label('Нужен именно файл profiles.ini, а не папка профиля. '
                     'Пустое поле включает автоматический поиск. Пути профилей читаются относительно выбранного файла.', 'muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        reset = QPushButton('Использовать автоматический поиск')
        reset.clicked.connect(self.path_edit.clear)
        layout.addWidget(reset, alignment=Qt.AlignmentFlag.AlignLeft)
        self.error_label = label('')
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet('color: #ffb18f;')
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('Сохранить')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Отмена')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def browse(self):
        default = self.path_edit.text() or str(Path(os.environ.get('APPDATA', '')) / 'Mozilla/Firefox/profiles.ini')
        filename, _ = QFileDialog.getOpenFileName(
            self, 'Выберите profiles.ini Firefox', default, 'Файл профилей Firefox (profiles.ini);;INI-файлы (*.ini)')
        if filename:
            self.path_edit.setText(filename)

    def accept(self):
        text = self.path_edit.text().strip().strip('"')
        try:
            if text:
                path = Path(os.path.expandvars(text)).expanduser().resolve()
                if path.name.lower() != 'profiles.ini' or not path.is_file():
                    raise BridgeError('Выберите существующий файл с именем profiles.ini.')
                firefox_profiles(path)
                text = str(path)
            updated = {'firefox_profiles_ini': text}
            save_settings(updated, self.save_path)
        except BridgeError as error:
            self.error_label.setText(str(error))
            return
        except OSError:
            self.error_label.setText('Не удалось сохранить настройки рядом с приложением. '
                                     'Переместите папку FoxBridge в доступную для записи папку.')
            return
        self.preferences = updated
        super().accept()


class Window(QMainWindow):
    def __init__(self, preferences_path=None, autoload=True):
        super().__init__()
        self.preferences_path = preferences_path
        self.preferences = load_settings(preferences_path)
        self.events = Events()
        self.events.message.connect(self.on_event)
        self.engine = Engine(self.events.message.emit)
        self.busy = False
        self.ready = False
        self.connected = False
        self.local_port = 8765
        self.last_warning = None
        self.session_id = None
        self.channel_health = None
        self.launch_timezone = None
        self.check_busy = False
        self.last_statistics = None
        self.last_attempt = None
        self.stats_error = ''
        self.stats_stale = True
        self.stats_dialog = None
        self.refresh_available_at = 0
        self.setWindowTitle('FoxBridge — локальный прокси Firefox')
        self.setWindowIcon(app_icon())
        self.resize(980, 960)
        self.setMinimumSize(820, 640)
        root = QWidget()
        root.setObjectName('root')
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(root)
        self.setCentralWidget(scroll)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(26, 22, 26, 20)
        layout.setSpacing(16)

        top = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(label('FoxBridge', 'brand'))
        titles.addWidget(label('Локальный прокси через встроенный VPN Firefox', 'muted'))
        top.addLayout(titles)
        top.addStretch()
        self.status = label('Не подключено', 'status')
        top.addWidget(self.status)
        self.settings_button = QPushButton('Настройки')
        self.settings_button.clicked.connect(self.open_settings)
        top.addWidget(self.settings_button)
        layout.addLayout(top)

        account_card, ac = card()
        row = QHBoxLayout()
        row.addWidget(label('Профиль Firefox', 'heading'))
        row.addStretch()
        self.account_label = label('Авторизация из браузера', 'muted')
        row.addWidget(self.account_label)
        ac.addLayout(row)
        row = QHBoxLayout()
        self.profiles = QComboBox()
        self.profiles.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.refresh = QPushButton('Обновить из Firefox')
        row.addWidget(self.profiles, 1)
        row.addWidget(self.refresh)
        ac.addLayout(row)
        self.quota_label = label('Квота: загрузите профиль')
        ac.addWidget(self.quota_label)
        self.quota_bar = QProgressBar()
        self.quota_bar.setRange(0, 1000)
        self.quota_bar.setValue(0)
        self.quota_bar.setTextVisible(False)
        ac.addWidget(self.quota_bar)
        self.reset_label = label('Данные о лимите запрашиваются у Mozilla', 'muted')
        ac.addWidget(self.reset_label)
        layout.addWidget(account_card)

        connection_card, cc = card()
        cc.addWidget(label('Подключение', 'heading'))
        row = QHBoxLayout()
        server_column = QVBoxLayout()
        server_column.addWidget(label('Серверы из профиля Firefox', 'muted'))
        self.servers = QComboBox()
        self.servers.setMinimumContentsLength(30)
        self.servers.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        server_column.addWidget(self.servers)
        row.addLayout(server_column, 1)
        port_column = QVBoxLayout()
        port_column.addWidget(label('Локальный порт', 'muted'))
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(8765)
        port_column.addWidget(self.port)
        row.addLayout(port_column)
        cc.addLayout(row)
        self.toggle = QPushButton('Включить прокси')
        self.toggle.setObjectName('primary')
        cc.addWidget(self.toggle)
        self.address = label('HTTP-прокси: 127.0.0.1:8765 · выключен', 'muted')
        self.address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cc.addWidget(self.address)
        row = QHBoxLayout()
        self.shell = QPushButton('Открыть PowerShell с прокси')
        self.copy = QPushButton('Скопировать команды')
        row.addWidget(self.shell, 1)
        row.addWidget(self.copy)
        cc.addLayout(row)
        note = label('Запускайте claude или codex в этом PowerShell. Для Codex автоматически выбирается отдельный процесс.', 'muted')
        note.setWordWrap(True)
        cc.addWidget(note)
        self.launch_note = label('Запуск терминала — после получения score и определения часового пояса.', 'muted')
        self.launch_note.setWordWrap(True)
        cc.addWidget(self.launch_note)
        self.channel_note = label('В обход прокси запросы FoxBridge не отправляются. Это не системный киллсвич.', 'muted')
        self.channel_note.setWordWrap(True)
        cc.addWidget(self.channel_note)
        self.retry_button = QPushButton('Повторить подключение сейчас')
        self.retry_button.clicked.connect(lambda: self.engine.submit('retry', session_id=self.session_id))
        self.retry_button.setVisible(False)
        cc.addWidget(self.retry_button)
        layout.addWidget(connection_card)

        check_card, ck = card()
        row = QHBoxLayout()
        row.addWidget(label('Score Net.Coffee', 'heading'))
        row.addStretch()
        self.check = QPushButton('Обновить статистику')
        row.addWidget(self.check)
        self.details_button = QPushButton('Подробности')
        self.details_button.clicked.connect(self.open_statistics)
        row.addWidget(self.details_button)
        ck.addLayout(row)
        self.score_label = label('— / 100', 'score')
        self.score_label.setToolTip('Итоговая оценка страницы Net.Coffee, включая её региональное правило. '
                                    'Исходная оценка API и пояснение находятся в подробностях.')
        ck.addWidget(self.score_label)
        self.score_bar = QProgressBar()
        self.score_bar.setRange(0, 100)
        self.score_bar.setValue(0)
        self.score_bar.setTextVisible(False)
        ck.addWidget(self.score_bar)
        self.stats_status = label('Включите прокси, чтобы получить score.', 'muted')
        self.stats_status.setWordWrap(True)
        ck.addWidget(self.stats_status)
        ck.addWidget(label('Выше — лучше по оценке Net.Coffee. Это не официальный рейтинг Anthropic.', 'muted'))
        layout.addWidget(check_card)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(100)
        self.log.document().setMaximumBlockCount(80)
        layout.addWidget(self.log, 1)
        foot = label('Учебный прототип · неофициальный клиент · HTTPS остаётся зашифрованным до сайта', 'muted')
        foot.setWordWrap(True)
        layout.addWidget(foot)

        self.refresh.clicked.connect(self.load_profile)
        self.profiles.currentIndexChanged.connect(self.profile_changed)
        self.toggle.clicked.connect(self.toggle_proxy)
        self.check.clicked.connect(lambda: self.run('check'))
        self.shell.clicked.connect(self.open_shell)
        self.copy.clicked.connect(self.copy_commands)
        self.port.valueChanged.connect(self.update_address)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self.quota_timer = QTimer(self)
        self.quota_timer.timeout.connect(self.refresh_quota)
        self.quota_timer.start(60000)
        self.update_controls()
        if autoload:
            QTimer.singleShot(0, self.discover)

    def discover(self):
        self.ready = False
        self.clear_statistics()
        self.profiles.blockSignals(True)
        self.profiles.clear()
        self.servers.clear()
        self.account_label.setText('Авторизация из браузера')
        self.quota_label.setText('Квота: загрузите профиль')
        self.quota_bar.setValue(0)
        self.reset_label.setText('Данные о лимите запрашиваются у Mozilla')
        try:
            profiles = firefox_profiles(self.preferences.get('firefox_profiles_ini'))
            for name, path, _ in profiles:
                self.profiles.addItem(name, str(path))
            self.profiles.blockSignals(False)
            self.load_profile()
        except Exception as error:
            self.status.setText('Проверьте путь к Firefox')
            self.append_log(explain_error(error))
        finally:
            self.profiles.blockSignals(False)
            self.update_controls()

    def open_settings(self):
        if self.connected or self.busy:
            return
        dialog = SettingsDialog(self.preferences, self, self.preferences_path)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.preferences = dialog.preferences
            self.append_log('Настройки сохранены. Обновляю список профилей Firefox.')
            self.discover()

    def append_log(self, text):
        self.log.append(f'{datetime.now():%H:%M:%S}  {text}')

    def profile_changed(self):
        self.ready = False
        self.servers.clear()
        self.load_profile()

    def run(self, operation, **kwargs):
        if operation == 'check':
            if not self.connected or self.busy or self.check_busy or self.cooldown():
                return
            self.check_busy = True
            self.refresh_available_at = time.monotonic() + 10
            self.render_score()
            self.update_controls()
            self.update_statistics_dialog()
            self.engine.submit('check', session_id=self.session_id)
            return
        if self.busy:
            return
        self.busy = True
        self.update_controls()
        statuses = {'load': 'Чтение профиля…', 'connect': 'Подключение…',
                    'disconnect': 'Отключение…'}
        if operation in statuses:
            self.status.setText(statuses[operation])
        self.engine.submit(operation, **kwargs)

    def load_profile(self):
        if self.profiles.currentData():
            self.ready = False
            self.run('load', profile=self.profiles.currentData())

    def toggle_proxy(self):
        if self.connected:
            self.run('disconnect')
        else:
            self.run('connect', server_index=self.servers.currentData(), port=self.port.value())

    def update_controls(self):
        self.settings_button.setEnabled(not self.busy and not self.connected)
        self.settings_button.setToolTip('Отключите прокси перед сменой источника профилей.' if self.connected else
                                       'Указать profiles.ini вручную или включить автоматический поиск.')
        self.refresh.setEnabled(not self.busy and not self.connected)
        self.profiles.setEnabled(not self.busy and not self.connected)
        self.servers.setEnabled(not self.busy and not self.connected and self.ready)
        self.port.setEnabled(not self.busy and not self.connected)
        self.toggle.setEnabled(not self.busy and (self.connected or self.ready))
        wait = self.cooldown()
        self.check.setEnabled(not self.busy and self.connected and not self.check_busy and not wait)
        self.check.setText('Обновляем…' if self.check_busy else
                           f'Обновить через {wait} с' if self.connected and wait else 'Обновить статистику')
        self.details_button.setEnabled(bool(self.connected or self.last_statistics or self.last_attempt))
        can_launch, reason = self.launch_readiness()
        self.shell.setEnabled(can_launch)
        self.copy.setEnabled(can_launch)
        self.shell.setToolTip(reason)
        self.copy.setToolTip(reason)
        self.launch_note.setText(reason)
        self.toggle.setText('Выключить прокси' if self.connected else 'Включить прокси')

    def launch_readiness(self):
        if not self.connected:
            return False, 'Для запуска терминала сначала включите прокси.'
        if self.busy or self.check_busy:
            return False, 'Ожидаем завершения проверки score и часового пояса…'
        if (self.channel_health or {}).get('state', 'online') != 'online':
            return False, 'Канал Firefox недоступен. Запуск нового терминала остановлен.'
        if (self.stats_stale or self.stats_error or not self.last_statistics or
                self.last_statistics.get('session_id') != self.session_id or
                self.last_statistics.get('score') is None):
            return False, 'Нужен актуальный score текущего подключения. Нажмите «Обновить статистику».'
        zone = self.launch_timezone or {}
        if not zone.get('id'):
            return False, zone.get('error', 'Часовой пояс не определён. Обновите статистику.')
        source = 'оценка по стране Net.Coffee' if zone.get('estimated') else 'поле timezone Net.Coffee'
        return True, (f'TZ нового терминала: {zone["id"]} ({zone["utc"]}; {source}). '
                      'Настройка окружения; универсальная подмена Windows API не выполняется. '
                      'После смены сервера откройте новый терминал.')

    def update_address(self):
        port = self.local_port if self.connected else self.port.value()
        state = ('порт активен · канал недоступен' if (self.channel_health or {}).get('state') == 'recovering'
                 else 'работает') if self.connected else 'выключен'
        self.address.setText(f'HTTP-прокси: 127.0.0.1:{port} · {state}')

    def show_quota(self, quota):
        if not quota:
            self.quota_label.setText('Квота: сервер не вернул сведения')
            self.quota_bar.setValue(0)
            return
        if quota.get('unlimited'):
            self.quota_label.setText('Без ограничения трафика по данным Mozilla')
            self.quota_bar.setValue(1000)
            self.reset_label.setText('Безлимитный доступ аккаунта')
            return
        remaining, maximum = quota['remaining'], quota['max']
        self.quota_label.setText(f'Осталось {remaining / 1024**3:.2f} ГиБ из {maximum / 1024**3:.2f} ГиБ')
        self.quota_bar.setValue(round(1000 * remaining / maximum) if maximum else 0)
        reset = quota.get('reset', '')
        try:
            reset = datetime.fromisoformat(reset.replace('Z', '+00:00')).astimezone().strftime('%d.%m.%Y %H:%M UTC%z')
        except ValueError:
            pass
        self.reset_label.setText(f'Сброс квоты: {reset or "не указан"} · данные Mozilla, обновление раз в минуту')

    def on_event(self, kind, data):
        if kind == 'health':
            if not self.connected or data.get('session_id') != self.session_id:
                return
            previous = self.channel_health or {}
            self.channel_health = data
            if data.get('state') == 'recovering':
                self.stats_stale = True
            if data.get('state') != previous.get('state') or data.get('code') != previous.get('code'):
                self.append_log('Канал Firefox восстановлен.' if data['state'] == 'online' else data['message'])
            self.render_channel()
            self.render_score()
            self.update_statistics_dialog()
            self.update_controls()
            if data['state'] == 'online' and previous.get('state') == 'recovering':
                QTimer.singleShot(0, self, lambda: self.run('check'))
            return
        if kind == 'retry':
            return
        if kind == 'warning':
            if data != self.last_warning:
                self.append_log(data)
                self.last_warning = data
            if self.connected:
                self.status.setText('Включён · есть ошибка')
            return
        if kind == 'quota':
            self.show_quota(data)
            return
        if kind == 'check' or (kind == 'error' and data['operation'] == 'check'):
            self.finish_statistics(kind, data)
            return
        self.busy = False
        if kind == 'error':
            self.append_log(data['message'])
            if data['operation'] == 'load':
                self.ready = False
            self.status.setText('Включён · ошибка проверки' if self.connected else 'Не подключено')
        elif kind == 'load':
            self.ready = True
            email = data['email']
            local, _, domain = email.partition('@')
            self.account_label.setText((local[:2] + '***@' + domain) if domain else 'Аккаунт Mozilla')
            self.servers.clear()
            selected = 0
            for index, server in enumerate(data['servers']):
                self.servers.addItem(server.label, index)
                if server.country == data['selected_country']:
                    selected = index
            self.servers.setCurrentIndex(selected)
            self.show_quota(data['quota'])
            self.status.setText('Готово к подключению')
            self.append_log(f'Профиль прочитан. Доступных серверов: {len(data["servers"])}.')
        elif kind == 'connect':
            self.clear_statistics()
            self.connected = True
            self.session_id = data['session_id']
            self.channel_health = data.get('health', {'state': 'online'})
            self.local_port = data['port']
            self.status.setText('Подключено')
            self.append_log('Прокси включён. TLS и авторизация сервера проверены.')
            QTimer.singleShot(0, self, lambda: self.run('check'))
        elif kind == 'disconnect':
            self.connected = False
            self.session_id = None
            self.channel_health = None
            self.check_busy = False
            self.stats_stale = True
            self.status.setText('Отключено')
            self.append_log('Прокси выключен. Активные соединения закрыты.')
        self.update_address()
        self.render_channel()
        self.update_controls()
        self.render_score()
        self.update_statistics_dialog()

    def cooldown(self):
        return max(0, math.ceil(self.refresh_available_at - time.monotonic()))

    def clear_statistics(self):
        self.launch_timezone = None
        self.last_statistics = None
        self.last_attempt = None
        self.stats_error = ''
        self.stats_stale = True
        self.check_busy = False
        self.refresh_available_at = 0
        self.render_score()
        self.update_statistics_dialog()

    def render_score(self):
        snapshot = self.last_statistics or {}
        score = snapshot.get('score')
        number = f'{score:g}' if isinstance(score, (int, float)) else '—'
        self.score_label.setText(f'{number} / 100')
        gray = self.stats_stale or self.check_busy or not self.connected
        color = '#8490a5' if gray or score is None else (
            '#22c55e' if score >= 95 else '#16a34a' if score >= 80 else
            '#84cc16' if score >= 50 else '#eab308' if score >= 25 else '#ef4444')
        self.score_label.setStyleSheet(f'color: {color};')
        self.score_bar.setStyleSheet(f'QProgressBar::chunk {{ background: {color}; border-radius: 5px; }}')
        if self.check_busy and score is None:
            self.score_bar.setRange(0, 0)
        else:
            self.score_bar.setRange(0, 100)
            self.score_bar.setValue(round(score) if score is not None else 0)
        when = time_text(snapshot.get('checked_at'))
        if self.check_busy:
            message = 'Получаем score через выбранный прокси…'
            if score is not None:
                message += f' Предыдущий результат: {when}.'
        elif not self.connected:
            message = f'Прокси выключен · сохранённый score от {when}.' if score is not None else 'Включите прокси, чтобы получить score.'
        elif (self.channel_health or {}).get('state') == 'recovering':
            message = ('Канал Firefox недоступен. ' + (f'Сохранённый score от {when}.' if score is not None else
                       'Статистика будет доступна после восстановления канала.'))
        elif self.stats_error:
            message = 'Score не обновлён. ' + self.stats_error
            if score is not None:
                message += f' Показан устаревший результат от {when}.'
        elif score is not None:
            message = f'Получено: {when}.'
            if snapshot.get('region_adjusted'):
                message += ' Применено региональное правило Net.Coffee — см. подробности.'
            elif snapshot.get('errors'):
                message += ' Часть дополнительных сведений недоступна.'
        else:
            message = 'Нажмите «Обновить статистику», чтобы получить score.'
        self.stats_status.setText(message)

    def finish_statistics(self, kind, data):
        if data.get('session_id') != self.session_id or not self.connected:
            return
        self.check_busy = False
        if data.get('discarded'):
            self.render_score()
            self.update_controls()
            return
        if kind == 'error':
            self.stats_error = data['message']
            self.last_attempt = {'checked_at': datetime.now(timezone.utc).isoformat(),
                                 'score': None, 'errors': {'request': data['message']}}
        else:
            self.last_attempt = data
            if data.get('score') is not None:
                self.launch_timezone = select_timezone(data)
                data['launch_timezone'] = self.launch_timezone
                self.last_statistics = data
                self.stats_error = ''
                self.stats_stale = False
                self.append_log('Статистика Net.Coffee обновлена.')
            else:
                self.stats_error = '; '.join(data.get('errors', {}).values()) or 'Сервис не вернул score.'
        if self.stats_error:
            self.stats_stale = True
            self.append_log('Проверка Net.Coffee: ' + self.stats_error)
        retry = data.get('retry_after', 0)
        if retry:
            self.refresh_available_at = max(self.refresh_available_at, time.monotonic() + retry)
        self.render_score()
        self.update_controls()
        self.update_statistics_dialog()

    def open_statistics(self):
        if self.stats_dialog is None:
            self.stats_dialog = StatisticsDialog(self)
            self.stats_dialog.refresh_requested.connect(lambda: self.run('check'))
        self.update_statistics_dialog()
        self.stats_dialog.show()
        self.stats_dialog.raise_()
        self.stats_dialog.activateWindow()

    def update_statistics_dialog(self):
        if self.stats_dialog is not None:
            self.stats_dialog.set_snapshot(
                self.last_statistics or self.last_attempt, self.last_attempt,
                self.stats_error, self.stats_stale, self.connected, self.check_busy,
                self.cooldown(), self.engine.stats())

    def tick(self):
        activity = self.engine.stats()
        if self.connected and activity and activity.get('health'):
            self.channel_health = activity['health']
        self.render_channel()
        self.update_controls()
        if self.stats_dialog is not None:
            self.stats_dialog.update_activity(self.engine.stats(), self.connected, self.check_busy, self.cooldown())

    def render_channel(self):
        health = self.channel_health or {}
        self.update_address()
        recovering = self.connected and health.get('state') == 'recovering'
        self.retry_button.setVisible(recovering)
        throttled = health.get('code') in ('quota', 'rate_limit') and health.get('retry_in', 0) > 0
        self.retry_button.setEnabled(not self.busy and not throttled)
        if recovering:
            names = {'auth': 'Нужен вход в Firefox', 'quota': 'Квота исчерпана',
                     'rate_limit': 'Ограничение сервера', 'tls': 'Ошибка сертификата'}
            self.status.setText(names.get(health.get('code'), 'Восстанавливаем канал'))
            self.channel_note.setText(health.get('message', '') +
                f' Новые запросы через прокси остановлены. Повторная проверка через {health.get("retry_in", 0)} с. '
                'Прямое подключение FoxBridge не использует.')
        elif self.connected:
            if not self.busy:
                self.status.setText('Подключено')
            self.channel_note.setText('Канал Firefox доступен. При сбое запросы через FoxBridge получат ошибку; '
                                      'после восстановления терминал можно использовать дальше.')
        else:
            self.channel_note.setText('Прокси выключен. Настройки ранее открытых терминалов сохраняются. '
                                      'Системная блокировка прямых соединений не включена.')

    def refresh_quota(self):
        if self.ready and not self.busy and not self.connected:
            self.engine.submit('quota')

    def commands(self):
        allowed, reason = self.launch_readiness()
        if not allowed:
            raise BridgeError(reason)
        return powershell_bootstrap(self.local_port, self.launch_timezone['id'])

    def copy_commands(self):
        if not self.launch_readiness()[0]:
            return
        QApplication.clipboard().setText(self.commands())
        self.append_log('Скопированы настройки прокси, TZ и запуска Codex для текущего PowerShell.')

    def open_shell(self):
        allowed, reason = self.launch_readiness()
        if not allowed:
            self.append_log(reason)
            return
        timezone = self.launch_timezone['id']
        env = proxy_environment(self.local_port, timezone=timezone)
        try:
            # A visible console is intentional: this is the terminal the user requested.
            subprocess.Popen(powershell_arguments(self.local_port, timezone), env=env,
                             creationflags=subprocess.CREATE_NEW_CONSOLE,
                             cwd=str(Path.home() / 'Documents'))
            self.append_log(f'Открыт PowerShell с прокси и TZ={timezone}. '
                            'Системный пояс не изменён; действие TZ зависит от программы.')
        except OSError:
            self.append_log('Не удалось открыть PowerShell. Используйте кнопку копирования команд.')

    def closeEvent(self, event):
        if self.busy:
            self.append_log('Дождитесь завершения текущей операции перед закрытием.')
            event.ignore()
            return
        self.timer.stop()
        self.quota_timer.stop()
        if self.stats_dialog is not None:
            self.stats_dialog.close()
        try:
            self.engine.shutdown()
        except Exception:
            pass
        event.accept()


def main():
    app = QApplication(sys.argv)
    # Also gives offscreen QA access to Windows fonts; nothing is installed.
    fonts = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
    for name in ('segoeui.ttf', 'segoeuib.ttf'):
        if (fonts / name).exists():
            QFontDatabase.addApplicationFont(str(fonts / name))
    app.setFont(QFont('Segoe UI', 10))
    app.setApplicationName('FoxBridge')
    app.setApplicationVersion('0.6')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    window = Window()
    window.show()
    if '--screenshot' in sys.argv:
        path = sys.argv[sys.argv.index('--screenshot') + 1]
        if '--connect' in sys.argv:
            timer = QTimer(window)
            def auto_connect():
                if window.ready and not window.busy and not window.connected:
                    window.toggle_proxy()
                    timer.stop()
            timer.timeout.connect(auto_connect)
            timer.start(300)
        def capture():
            if window.busy or window.check_busy:
                QTimer.singleShot(1000, capture)
                return
            window.grab().save(path)
            window.close()
        QTimer.singleShot(12000, capture)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
