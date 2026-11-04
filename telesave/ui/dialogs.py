"""Диалоги: первый запуск, разблокировка, вход в Telegram, период, экспорт, версии, пароли."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QRadioButton, QStackedWidget, QTextBrowser, QVBoxLayout,
                               QWidget, QDateEdit)

from ..crypto import KeyStore, WrongPassword
from ..paths import default_data_dir
from .widgets import DateRange, fmt_dt


def _title(text, name="H1"):
    lbl = QLabel(text)
    lbl.setObjectName(name)
    lbl.setWordWrap(True)
    return lbl


def _muted(text):
    lbl = QLabel(text)
    lbl.setObjectName("Muted")
    lbl.setWordWrap(True)
    lbl.setOpenExternalLinks(True)
    return lbl


def _buttons(dlg, ok_text="Готово", cancel_text="Отмена"):
    h = QHBoxLayout()
    h.addStretch(1)
    cancel = QPushButton(cancel_text)
    cancel.clicked.connect(dlg.reject)
    ok = QPushButton(ok_text)
    ok.setObjectName("Primary")
    ok.setDefault(True)
    h.addWidget(cancel)
    h.addWidget(ok)
    return h, ok


def _pw_edit(placeholder):
    e = QLineEdit()
    e.setEchoMode(QLineEdit.Password)
    e.setPlaceholderText(placeholder)
    return e


# ================================================================ первый запуск
class SetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("TeleSave — первый запуск")
        self.setMinimumWidth(520)
        self.data_dir: Path | None = None
        self.password: str | None = None
        self.existing = False
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 24, 26, 20)
        v.setSpacing(10)
        v.addWidget(_title("Добро пожаловать в TeleSave"))
        v.addWidget(_muted("TeleSave хранит копии ваших переписок Telegram у вас на компьютере. "
                           "Сохранённые сообщения останутся в архиве, даже если их позже удалят в Telegram."))
        v.addSpacing(6)
        v.addWidget(_title("Папка для хранения архива", "H2"))
        h = QHBoxLayout()
        self.path = QLineEdit(str(default_data_dir()))
        browse = QPushButton("Обзор…")
        browse.clicked.connect(self._browse)
        h.addWidget(self.path, 1)
        h.addWidget(browse)
        v.addLayout(h)
        v.addSpacing(6)
        v.addWidget(_title("Защита архива", "H2"))
        v.addWidget(_muted("Архив всегда зашифрован. Без пароля ключ защищён вашей учётной записью Windows. "
                           "С паролем архив нельзя открыть, не зная его (восстановить забытый пароль невозможно)."))
        self.use_pw = QCheckBox("Запрашивать пароль при открытии архива")
        v.addWidget(self.use_pw)
        self.pw1, self.pw2 = _pw_edit("Пароль (не менее 6 символов)"), _pw_edit("Повторите пароль")
        for e in (self.pw1, self.pw2):
            e.setVisible(False)
            v.addWidget(e)
        self.use_pw.toggled.connect(lambda on: (self.pw1.setVisible(on), self.pw2.setVisible(on), self.adjustSize()))
        self.err = QLabel()
        self.err.setObjectName("Error")
        self.err.setWordWrap(True)
        v.addWidget(self.err)
        h2, ok = _buttons(self, "Продолжить", "Выход")
        ok.clicked.connect(self._accept)
        v.addLayout(h2)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Выберите папку для архива", self.path.text())
        if d:
            self.path.setText(str(Path(d) / "TeleSave") if not (Path(d) / KeyStore.FILENAME).exists() else d)

    def _accept(self):
        raw = self.path.text().strip()
        if not raw:
            self.err.setText("Укажите папку")
            return
        p = Path(raw).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
            test = p / ".write_test"
            test.write_text("ok")
            test.unlink()
        except Exception as e:
            self.err.setText(f"Нет доступа к папке: {e}")
            return
        if KeyStore(p).exists():
            QMessageBox.information(self, "TeleSave", "В этой папке уже есть архив TeleSave — он будет открыт.")
            self.existing = True
        elif self.use_pw.isChecked():
            if len(self.pw1.text()) < 6:
                self.err.setText("Пароль должен быть не короче 6 символов")
                return
            if self.pw1.text() != self.pw2.text():
                self.err.setText("Пароли не совпадают")
                return
            self.password = self.pw1.text()
        self.data_dir = p
        self.accept()


class UnlockDialog(QDialog):
    def __init__(self, keystore: KeyStore, parent=None):
        super().__init__(parent)
        self.ks, self.master = keystore, None
        self.setWindowTitle("TeleSave — архив защищён")
        self.setMinimumWidth(420)
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 24, 26, 20)
        v.setSpacing(10)
        v.addWidget(_title("Введите пароль архива"))
        v.addWidget(_muted(f"Папка: {keystore.path.parent}"))
        self.pw = _pw_edit("Пароль")
        v.addWidget(self.pw)
        self.err = QLabel()
        self.err.setObjectName("Error")
        v.addWidget(self.err)
        other = QPushButton("Открыть другую папку…")
        other.setObjectName("Link")
        other.clicked.connect(lambda: self.done(2))
        v.addWidget(other, 0, Qt.AlignLeft)
        h, ok = _buttons(self, "Открыть", "Выход")
        ok.clicked.connect(self._accept)
        v.addLayout(h)

    def _accept(self):
        try:
            self.master = self.ks.unlock(self.pw.text())
            self.accept()
        except WrongPassword:
            self.err.setText("Неверный пароль")
            self.pw.selectAll()
            self.pw.setFocus()


# ================================================================ вход Telegram
class LoginDialog(QDialog):
    def __init__(self, ctx, parent=None, note: str = ""):
        super().__init__(parent)
        self.ctx, self.w = ctx, ctx.worker
        self.setWindowTitle("Вход в Telegram")
        self.setMinimumWidth(500)
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 24, 26, 20)
        v.setSpacing(10)
        v.addWidget(_title("Вход в Telegram"))
        if note:
            v.addWidget(_muted(note))
        self.stack = QStackedWidget()
        v.addWidget(self.stack)
        self.status = QLabel()
        self.status.setWordWrap(True)
        v.addWidget(self.status)

        # 0 — API
        p0 = QWidget()
        l0 = QVBoxLayout(p0)
        l0.setContentsMargins(0, 0, 0, 0)
        l0.addWidget(_muted(
            "TeleSave подключается к Telegram через официальный API от вашего имени. "
            "Для этого нужны личные ключи приложения (один раз):<br>"
            "1. Откройте <a href='https://my.telegram.org'>my.telegram.org</a> и войдите по номеру телефона.<br>"
            "2. Выберите «API development tools».<br>"
            "3. Создайте приложение (название любое, например TeleSave, платформа Desktop).<br>"
            "4. Скопируйте сюда <b>api_id</b> и <b>api_hash</b>. Никому их не показывайте."))
        self.api_id = QLineEdit()
        self.api_id.setPlaceholderText("api_id (число)")
        self.api_hash = QLineEdit()
        self.api_hash.setPlaceholderText("api_hash (32 символа)")
        l0.addWidget(self.api_id)
        l0.addWidget(self.api_hash)
        self.stack.addWidget(p0)

        # 1 — телефон
        p1 = QWidget()
        l1 = QVBoxLayout(p1)
        l1.setContentsMargins(0, 0, 0, 0)
        l1.addWidget(_muted("Введите номер телефона аккаунта Telegram в международном формате."))
        self.phone = QLineEdit()
        self.phone.setPlaceholderText("+7 900 000-00-00")
        l1.addWidget(self.phone)
        change = QPushButton("Изменить API ID / Hash")
        change.setObjectName("Link")
        change.clicked.connect(lambda: self._go(0))
        l1.addWidget(change, 0, Qt.AlignLeft)
        self.stack.addWidget(p1)

        # 2 — код
        p2 = QWidget()
        l2 = QVBoxLayout(p2)
        l2.setContentsMargins(0, 0, 0, 0)
        self.code_info = _muted("")
        l2.addWidget(self.code_info)
        self.code = QLineEdit()
        self.code.setPlaceholderText("Код из Telegram")
        l2.addWidget(self.code)
        again = QPushButton("Указать другой номер")
        again.setObjectName("Link")
        again.clicked.connect(lambda: self._go(1))
        l2.addWidget(again, 0, Qt.AlignLeft)
        self.stack.addWidget(p2)

        # 3 — облачный пароль
        p3 = QWidget()
        l3 = QVBoxLayout(p3)
        l3.setContentsMargins(0, 0, 0, 0)
        self.pw_info = _muted("Включена двухэтапная аутентификация. Введите облачный пароль Telegram.")
        l3.addWidget(self.pw_info)
        self.cloud_pw = _pw_edit("Облачный пароль")
        l3.addWidget(self.cloud_pw)
        self.stack.addWidget(p3)

        h, self.ok = _buttons(self, "Далее", "Позже")
        self.ok.clicked.connect(self._next)
        v.addLayout(h)
        self.w.login_step.connect(self._on_step)
        self._go(1 if self.w.has_credentials() else 0)

    def _go(self, i):
        self.stack.setCurrentIndex(i)
        self._busy(False)
        self.status.setText("")
        [self.api_id, self.phone, self.code, self.cloud_pw][i].setFocus()

    def _busy(self, on, text=""):
        self.ok.setEnabled(not on)
        self.status.setObjectName("Muted")
        self.status.setStyleSheet("")
        self.status.setText(text)

    def _error(self, text):
        self._busy(False)
        self.status.setObjectName("Error")
        self.status.setStyleSheet("")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.setText(text)

    def _next(self):
        i = self.stack.currentIndex()
        if i == 0:
            aid, ah = self.api_id.text().strip(), self.api_hash.text().strip()
            if not aid.isdigit() or len(ah) < 20:
                self._error("Проверьте api_id (только цифры) и api_hash")
                return
            self.w.set_credentials(int(aid), ah)
            self._go(1)
        elif i == 1:
            ph = "".join(ch for ch in self.phone.text() if ch.isdigit() or ch == "+")
            if len(ph) < 8:
                self._error("Введите номер полностью, с кодом страны")
                return
            self._busy(True, "Запрашиваем код…")
            self.w.request_code(ph)
        elif i == 2:
            if not self.code.text().strip():
                return
            self._busy(True, "Проверяем код…")
            self.w.submit_code(self.code.text())
        elif i == 3:
            if not self.cloud_pw.text():
                return
            self._busy(True, "Проверяем пароль…")
            self.w.submit_password(self.cloud_pw.text())

    def _on_step(self, step, text):
        if step == "code":
            self.code_info.setText(text + " Введите его ниже.")
            self._go(2)
        elif step == "password":
            self.pw_info.setText("Включена двухэтапная аутентификация. Введите облачный пароль Telegram."
                                 + (f"\nПодсказка: {text}" if text else ""))
            self._go(3)
        elif step == "done":
            self.accept()
        elif step == "error":
            self._error(text)

    def done(self, r):
        try:
            self.w.login_step.disconnect(self._on_step)
        except Exception:
            pass
        super().done(r)


# ===================================================================== прочее
class PeriodDialog(QDialog):
    def __init__(self, parent=None, title="Сохранить сообщения за период"):
        super().__init__(parent)
        self.setWindowTitle(title)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 18)
        v.addWidget(_title(title, "H2"))
        v.addWidget(_muted("Будут загружены из Telegram все доступные сообщения в этом интервале."))
        h = QHBoxLayout()
        today = dt.date.today()
        self.d1, self.d2 = QDateEdit(), QDateEdit()
        for d in (self.d1, self.d2):
            d.setCalendarPopup(True)
            d.setDisplayFormat("dd.MM.yyyy")
        self.d1.setDate(today - dt.timedelta(days=30))
        self.d2.setDate(today)
        h.addWidget(QLabel("С"))
        h.addWidget(self.d1)
        h.addWidget(QLabel("по"))
        h.addWidget(self.d2)
        h.addStretch(1)
        v.addLayout(h)
        hb, ok = _buttons(self, "Загрузить")
        ok.clicked.connect(self.accept)
        v.addLayout(hb)

    def range(self):
        a, b = self.d1.date(), self.d2.date()
        t1 = dt.datetime(a.year(), a.month(), a.day()).astimezone()
        t2 = dt.datetime(b.year(), b.month(), b.day(), 23, 59, 59).astimezone()
        return min(t1, t2), max(t1, t2)


class ExportDialog(QDialog):
    def __init__(self, chat_title, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Экспорт переписки")
        self.setMinimumWidth(460)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 18)
        v.setSpacing(8)
        v.addWidget(_title(f"Экспорт: {chat_title}", "H2"))
        self.group = QButtonGroup(self)
        for i, (key, text) in enumerate((("html", "HTML — удобно читать в браузере"),
                                         ("txt", "TXT — простой текст"),
                                         ("json", "JSON — для программ и обработки"))):
            rb = QRadioButton(text)
            rb.setProperty("fmt", key)
            rb.setChecked(i == 0)
            self.group.addButton(rb)
            v.addWidget(rb)
        self.media = QCheckBox("Выгрузить скачанные вложения рядом с файлом")
        v.addWidget(self.media)
        self.dates = DateRange("Только за период")
        v.addWidget(self.dates)
        v.addWidget(_muted("Экспорт создаётся в незашифрованном виде — храните его бережно."))
        hb, ok = _buttons(self, "Экспортировать…")
        ok.clicked.connect(self.accept)
        v.addLayout(hb)

    @property
    def fmt(self) -> str:
        return self.group.checkedButton().property("fmt")


class VersionsDialog(QDialog):
    def __init__(self, versions, current: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("История изменений")
        self.resize(520, 420)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.addWidget(_title("История изменений сообщения", "H2"))
        tb = QTextBrowser()
        import html as _h
        parts = []
        if not versions:
            parts.append("<p><i>Сообщение было изменено, но предыдущая версия не была записана "
                         "(правка произошла до сохранения).</i></p>")
        for ed, text in versions:
            parts.append(f"<p><b>{fmt_dt(ed)}</b><br>{_h.escape(text).replace(chr(10), '<br>')}</p><hr>")
        parts.append(f"<p><b>Текущая версия</b><br>{_h.escape(current).replace(chr(10), '<br>')}</p>")
        tb.setHtml("".join(parts))
        v.addWidget(tb)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        v.addWidget(close, 0, Qt.AlignRight)


class PasswordDialog(QDialog):
    """Установка/смена/снятие пароля архива. mode: set | change | remove"""

    def __init__(self, ks: KeyStore, mode: str, parent=None):
        super().__init__(parent)
        self.ks, self.mode, self.new_password = ks, mode, None
        titles = {"set": "Установить пароль архива", "change": "Сменить пароль архива", "remove": "Убрать пароль архива"}
        self.setWindowTitle(titles[mode])
        self.setMinimumWidth(420)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 18)
        v.setSpacing(8)
        v.addWidget(_title(titles[mode], "H2"))
        self.cur = _pw_edit("Текущий пароль")
        self.p1, self.p2 = _pw_edit("Новый пароль (не менее 6 символов)"), _pw_edit("Повторите новый пароль")
        if mode in ("change", "remove"):
            v.addWidget(self.cur)
        if mode in ("set", "change"):
            v.addWidget(self.p1)
            v.addWidget(self.p2)
            v.addWidget(_muted("Запомните пароль: без него открыть архив невозможно."))
        else:
            v.addWidget(_muted("Ключ архива снова будет защищён вашей учётной записью Windows."))
        self.err = QLabel()
        self.err.setObjectName("Error")
        v.addWidget(self.err)
        hb, ok = _buttons(self, "Сохранить")
        ok.clicked.connect(self._accept)
        v.addLayout(hb)

    def _accept(self):
        if self.mode in ("change", "remove"):
            try:
                self.ks.unlock(self.cur.text())
            except WrongPassword:
                self.err.setText("Неверный текущий пароль")
                return
        if self.mode in ("set", "change"):
            if len(self.p1.text()) < 6:
                self.err.setText("Пароль должен быть не короче 6 символов")
                return
            if self.p1.text() != self.p2.text():
                self.err.setText("Пароли не совпадают")
                return
            self.new_password = self.p1.text()
        self.accept()


class BackupPasswordDialog(QDialog):
    def __init__(self, confirm: bool, parent=None):
        super().__init__(parent)
        self.confirm, self.password = confirm, None
        self.setWindowTitle("Пароль резервной копии")
        self.setMinimumWidth(420)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 18)
        v.setSpacing(8)
        if confirm:
            v.addWidget(_title("Пароль для резервной копии", "H2"))
            v.addWidget(_muted("Копия будет зашифрована. Этот пароль понадобится для восстановления "
                               "(в том числе на другом компьютере)."))
        else:
            v.addWidget(_title("Восстановление из резервной копии", "H2"))
            v.addWidget(_muted("Введите пароль, заданный при создании копии. Текущий архив будет "
                               "перенесён в отдельную папку, а не удалён."))
        self.p1, self.p2 = _pw_edit("Пароль копии"), _pw_edit("Повторите пароль")
        v.addWidget(self.p1)
        if confirm:
            v.addWidget(self.p2)
        self.err = QLabel()
        self.err.setObjectName("Error")
        v.addWidget(self.err)
        hb, ok = _buttons(self, "Продолжить")
        ok.clicked.connect(self._accept)
        v.addLayout(hb)

    def _accept(self):
        if self.confirm:
            if len(self.p1.text()) < 6:
                self.err.setText("Пароль должен быть не короче 6 символов")
                return
            if self.p1.text() != self.p2.text():
                self.err.setText("Пароли не совпадают")
                return
        elif not self.p1.text():
            return
        self.password = self.p1.text()
        self.accept()


def ask_save_path(parent, title, default_name, filt) -> Path | None:
    path, _ = QFileDialog.getSaveFileName(parent, title, str(Path(os.path.expanduser("~")) / default_name), filt)
    return Path(path) if path else None
