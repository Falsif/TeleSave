"""Страницы главного окна."""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QListView, QMenu, QMessageBox, QPushButton, QScrollArea, QSpinBox,
                               QSplitter, QStackedWidget, QToolButton, QVBoxLayout, QWidget, QCheckBox)

from .. import autostart
from ..backup import create_backup, read_backup_key, restore_backup
from ..crypto import WrongPassword
from ..exporter import export_chat, safe_name
from . import theme
from .dialogs import BackupPasswordDialog, ExportDialog, PasswordDialog, PeriodDialog, ask_save_path
from .icons import icon
from .widgets import (KIND_NAMES, MEDIA_ICONS, MEDIA_NAMES, AvatarLabel, DateRange, MessageList, ToggleSwitch,
                      fmt_ago, fmt_dt, fmt_size)


def _lbl(text, name=None, wrap=False):
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    return lbl


def _btn(text, icon_name=None, primary=False, tip=None):
    b = QPushButton(text)
    if icon_name:
        b.setIcon(icon(icon_name, theme.C["on_accent"] if primary else theme.C["text"], 16))
    if primary:
        b.setObjectName("Primary")
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


class Page(QWidget):
    def __init__(self, ctx, title, subtitle=""):
        super().__init__()
        self.ctx = ctx
        self.setObjectName("Page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(24, 20, 24, 12)
        self.root.setSpacing(10)
        self.root.addWidget(_lbl(title, "H1"))
        if subtitle:
            self.root.addWidget(_lbl(subtitle, "Muted", True))

    def on_show(self):
        pass

    def retheme(self):
        self.on_show()


# ======================================================== Все переписки
class ChatRow(QWidget):
    def __init__(self, ctx, chat, page):
        super().__init__()
        self.ctx, self.chat, self.page = ctx, chat, page
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 7, 10, 7)
        h.setSpacing(12)
        av = AvatarLabel(40)
        av.set_chat(chat["avatar"], chat["title"], chat["id"])
        h.addWidget(av)
        col = QVBoxLayout()
        col.setSpacing(1)
        t = _lbl(chat["title"])
        t.setStyleSheet("font-weight:600")
        col.addWidget(t)
        parts = [KIND_NAMES.get(chat["kind"], "")]
        if chat.get("cnt"):
            parts.append(f"сохранено {chat['cnt']}")
        if chat.get("selected"):
            parts.append("синхр.: " + fmt_ago(chat.get("last_sync")))
        col.addWidget(_lbl(" · ".join(p for p in parts if p), "Small"))
        h.addLayout(col, 1)
        h.addWidget(_lbl("Сохранять", "Small"))
        self.toggle = ToggleSwitch()
        self.toggle.set_quiet(bool(chat["selected"]))
        self.toggle.toggled.connect(lambda v: ctx.set_selected(chat["id"], v))
        h.addWidget(self.toggle)
        more = QToolButton()
        more.setIcon(icon("more", theme.C["muted"]))
        more.setCursor(Qt.PointingHandCursor)
        more.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(more)
        menu.addAction(icon("download", theme.C["text"]), "Сохранить всю доступную историю",
                       lambda: page.fetch_all(chat))
        menu.addAction(icon("history", theme.C["text"]), "Сохранить за период…", lambda: page.fetch_period(chat))
        if chat.get("cnt"):
            menu.addAction(icon("archive", theme.C["text"]), "Открыть в архиве", lambda: ctx.open_chat(chat["id"]))
        more.setMenu(menu)
        h.addWidget(more)


class ChatsPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "Все переписки",
                         "Включите «Сохранять» у чатов, которые нужно архивировать. Новые сообщения в них "
                         "будут записываться автоматически, пока TeleSave запущен (в том числе в трее).")
        bar = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Фильтр по названию…")
        self.filter.textChanged.connect(lambda: self._timer.start(200))
        self.kind = QComboBox()
        self.kind.addItems(["Все", "Выбранные", "Личные", "Группы", "Каналы"])
        self.kind.currentIndexChanged.connect(self.reload)
        refresh = _btn("Обновить список", "refresh")
        refresh.clicked.connect(lambda: ctx.worker.refresh_dialogs())
        sync = _btn("Синхронизировать", "sync", primary=True)
        sync.clicked.connect(lambda: ctx.worker.sync_now())
        bar.addWidget(self.filter, 1)
        bar.addWidget(self.kind)
        bar.addWidget(refresh)
        bar.addWidget(sync)
        self.root.addLayout(bar)
        self.info = _lbl("", "Small")
        self.root.addWidget(self.info)
        self.stack = QStackedWidget()
        self.list = QListWidget()
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.setSelectionMode(QListWidget.NoSelection)
        self.stack.addWidget(self.list)
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.addStretch(1)
        self.empty_lbl = _lbl("", "Muted", True)
        self.empty_lbl.setAlignment(Qt.AlignCenter)
        ev.addWidget(self.empty_lbl)
        self.login_btn = _btn("Войти в Telegram", "user", primary=True)
        self.login_btn.clicked.connect(lambda: ctx.main.show_login())
        ev.addWidget(self.login_btn, 0, Qt.AlignCenter)
        ev.addStretch(2)
        self.stack.addWidget(empty)
        self.root.addWidget(self.stack, 1)
        self._timer = QTimer(self, singleShot=True, interval=300)
        self._timer.timeout.connect(self.reload)

    def schedule_reload(self):
        self._timer.start(400)

    def on_show(self):
        self.reload()

    def reload(self):
        chats = self.ctx.db.list_chats()
        q = self.filter.text().casefold().strip()
        k = self.kind.currentIndex()
        kinds = {2: ("user", "bot"), 3: ("group", "supergroup"), 4: ("channel",)}
        rows = [c for c in chats if (not q or q in c["title"].casefold())
                and (k != 1 or c["selected"]) and (k not in kinds or c["kind"] in kinds[k])]
        sel = sum(1 for c in chats if c["selected"])
        self.info.setText(f"Переписок: {len(chats)} · сохраняются: {sel}" + (f" · показано: {len(rows)}" if q or k else ""))
        if not chats:
            logged = self.ctx.worker.has_session()
            self.empty_lbl.setText("Список переписок загружается…" if logged else
                                   "Чтобы увидеть переписки, войдите в свой аккаунт Telegram.")
            self.login_btn.setVisible(not logged)
            self.stack.setCurrentIndex(1)
            return
        self.stack.setCurrentIndex(0)
        pos = self.list.verticalScrollBar().value()
        self.list.setUpdatesEnabled(False)
        self.list.clear()
        for c in rows:
            it = QListWidgetItem()
            w = ChatRow(self.ctx, c, self)
            it.setSizeHint(QSize(100, 58))
            self.list.addItem(it)
            self.list.setItemWidget(it, w)
        self.list.setUpdatesEnabled(True)
        QTimer.singleShot(0, lambda: self.list.verticalScrollBar().setValue(pos))

    def fetch_all(self, chat):
        if QMessageBox.question(self, "TeleSave", f"Загрузить всю доступную историю «{chat['title']}»?\n"
                                "Для больших чатов это может занять время — прогресс виден внизу окна.") \
                == QMessageBox.Yes:
            self.ctx.worker.fetch_history(chat["id"])

    def fetch_period(self, chat):
        d = PeriodDialog(self)
        if d.exec():
            a, b = d.range()
            self.ctx.worker.fetch_history(chat["id"], a, b)


# ===================================================== Сохранённые сообщения
class ArchiveChatRow(QWidget):
    def __init__(self, chat):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 6, 8, 6)
        h.setSpacing(10)
        av = AvatarLabel(38)
        av.set_chat(chat["avatar"], chat["title"], chat["id"])
        h.addWidget(av)
        col = QVBoxLayout()
        col.setSpacing(1)
        t = _lbl(chat["title"])
        t.setStyleSheet("font-weight:600")
        col.addWidget(t)
        sub = f"{chat['cnt']} сообщ."
        if chat.get("del"):
            sub += f" · удалено: {chat['del']}"
        col.addWidget(_lbl(sub, "Small"))
        h.addLayout(col, 1)


class ArchivePage(QWidget):
    PAGE = 80

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setObjectName("Page")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.cid = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter()
        split.setChildrenCollapsible(False)
        lay.addWidget(split)

        left = QWidget()
        left.setObjectName("Panel")
        left.setAttribute(Qt.WA_StyledBackground, True)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(10, 16, 6, 10)
        lv.setSpacing(8)
        lv.addWidget(_lbl("  Сохранённые сообщения", "H2"))
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Найти чат…")
        self.filter.textChanged.connect(self._fill_chats)
        lv.addWidget(self.filter)
        self.chats = QListWidget()
        self.chats.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.chats.currentItemChanged.connect(self._chat_clicked)
        lv.addWidget(self.chats, 1)
        left.setMinimumWidth(240)
        left.setMaximumWidth(380)
        split.addWidget(left)

        self.right = QStackedWidget()
        ph = QWidget()
        pv = QVBoxLayout(ph)
        self.placeholder = _lbl("Выберите чат слева, чтобы прочитать сохранённые сообщения.\n"
                                "Архив доступен и без интернета.", "Muted", True)
        self.placeholder.setAlignment(Qt.AlignCenter)
        pv.addWidget(self.placeholder)
        self.right.addWidget(ph)

        content = QWidget()
        cv = QVBoxLayout(content)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        header = QFrame()
        header.setObjectName("ChatHeader")
        hh = QHBoxLayout(header)
        hh.setContentsMargins(16, 10, 16, 10)
        self.h_avatar = AvatarLabel(42)
        hh.addWidget(self.h_avatar)
        hcol = QVBoxLayout()
        hcol.setSpacing(0)
        self.h_title = _lbl("", "H2")
        self.h_info = _lbl("", "Small", True)
        hcol.addWidget(self.h_title)
        hcol.addWidget(self.h_info)
        hh.addLayout(hcol, 1)
        hist = _btn("Загрузить историю", "download")
        hmenu = QMenu(hist)
        hmenu.addAction("Всю доступную историю", self._fetch_all)
        hmenu.addAction("За период…", self._fetch_period)
        hist.setMenu(hmenu)
        exp = _btn("Экспорт", "export")
        exp.clicked.connect(self._export)
        hh.addWidget(hist)
        hh.addWidget(exp)
        cv.addWidget(header)

        tools = QFrame()
        tools.setObjectName("ChatHeader")
        tv = QVBoxLayout(tools)
        tv.setContentsMargins(16, 6, 16, 8)
        tv.setSpacing(6)
        th, th2 = QHBoxLayout(), QHBoxLayout()
        tv.addLayout(th)
        tv.addLayout(th2)
        self.q = QLineEdit()
        self.q.setPlaceholderText("Поиск по словам в этой переписке…")
        self.q.returnPressed.connect(lambda: self.load())
        self.dates = DateRange()
        self.only_del = QCheckBox("Только удалённые")
        find = _btn("Найти", "search")
        find.clicked.connect(lambda: self.load())
        reset = _btn("Сброс")
        reset.clicked.connect(self._reset)
        th.addWidget(self.q, 1)
        th.addWidget(find)
        th.addWidget(reset)
        th2.addWidget(self.dates)
        th2.addSpacing(10)
        th2.addWidget(self.only_del)
        th2.addStretch(1)
        cv.addWidget(tools)
        self.view = MessageList(ctx)
        self.view.load_more.connect(self._more)
        cv.addWidget(self.view, 1)
        self.right.addWidget(content)
        split.addWidget(self.right)
        split.setStretchFactor(1, 1)
        split.setSizes([290, 900])
        self._rows: list = []
        self._upd = QTimer(self, singleShot=True, interval=500)
        self._upd.timeout.connect(self._live_update)
        self._pending_reload_chats = False

    # ---- список чатов
    def on_show(self):
        self.reload_chats()

    def retheme(self):
        self.reload_chats()
        if self.cid is not None:
            self.load()

    def reload_chats(self):
        self._rows = self.ctx.db.archived_chats()
        self._fill_chats()

    def _fill_chats(self):
        q = self.filter.text().casefold().strip()
        self.chats.blockSignals(True)
        self.chats.clear()
        current = None
        for c in self._rows:
            if q and q not in c["title"].casefold():
                continue
            it = QListWidgetItem()
            it.setData(Qt.UserRole, c["id"])
            it.setSizeHint(QSize(100, 54))
            self.chats.addItem(it)
            self.chats.setItemWidget(it, ArchiveChatRow(c))
            if c["id"] == self.cid:
                current = it
        if current is not None:
            self.chats.setCurrentItem(current)
        self.chats.blockSignals(False)
        if not self._rows:
            self.placeholder.setText("Архив пока пуст.\nОткройте «Все переписки» и включите сохранение нужных чатов.")

    def _chat_clicked(self, cur, _prev):
        if cur is not None:
            self.open_chat(cur.data(Qt.UserRole))

    # ---- сообщения
    def open_chat(self, cid, focus_msg=None):
        self.cid = cid
        if not self._rows or all(c["id"] != cid for c in self._rows):
            self.reload_chats()
        else:
            for i in range(self.chats.count()):
                if self.chats.item(i).data(Qt.UserRole) == cid:
                    self.chats.blockSignals(True)
                    self.chats.setCurrentRow(i)
                    self.chats.blockSignals(False)
        chat = self.ctx.db.get_chat(cid)
        if not chat:
            return
        self.h_avatar.set_chat(chat["avatar"], chat["title"], cid)
        self.h_title.setText(chat["title"])
        self._update_info(chat)
        self.right.setCurrentIndex(1)
        if focus_msg is not None:
            self._reset(load=False)
            self.load(focus=focus_msg)
        else:
            self.load()

    def _update_info(self, chat=None):
        chat = chat or self.ctx.db.get_chat(self.cid)
        row = next((c for c in self._rows if c["id"] == self.cid), None)
        cnt = row["cnt"] if row else 0
        info = f"{KIND_NAMES.get(chat['kind'], '')} · сохранено {cnt}"
        info += " · последняя синхронизация: " + fmt_ago(chat.get("last_sync"))
        if not chat.get("selected"):
            info += " · автосохранение выключено"
        self.h_info.setText(info)

    def _reset(self, load=True):
        self.q.clear()
        self.dates.set_active(False)
        self.only_del.setChecked(False)
        if load:
            self.load()

    def _filtered(self):
        return bool(self.q.text().strip()) or self.dates.chk.isChecked() or self.only_del.isChecked()

    def load(self, focus=None):
        if self.cid is None:
            return
        db = self.ctx.db
        d1, d2 = self.dates.range()
        if self.q.text().strip():
            msgs = db.search(self.q.text(), chat_id=self.cid, date_from=d1, date_to=d2,
                             only_deleted=self.only_del.isChecked(), limit=500)
            self.view.empty_text = "Ничего не найдено"
            self.view.set_messages(msgs, False)
            return
        self.view.empty_text = "Нет сообщений" if self._filtered() else "Здесь пока нет сохранённых сообщений"
        if focus is not None:
            m = db.get_message(self.cid, focus)
            if m:
                older, more = db.get_messages(self.cid, before=(m.date, m.msg_id + 1), limit=60)
                newer = db.get_messages_after(self.cid, (m.date, m.msg_id), limit=300)
                self.view.set_messages(older + newer, more, focus=(self.cid, focus))
                return
        msgs, more = db.get_messages(self.cid, limit=self.PAGE, date_from=d1, date_to=d2,
                                     only_deleted=self.only_del.isChecked())
        self.view.set_messages(msgs, more)

    def _more(self):
        if not self.view.msgs:
            return
        first = self.view.msgs[0]
        d1, d2 = self.dates.range()
        older, more = self.ctx.db.get_messages(self.cid, before=(first.date, first.msg_id), limit=self.PAGE,
                                               date_from=d1, date_to=d2, only_deleted=self.only_del.isChecked())
        self.view.prepend(older, more)

    # ---- живые обновления
    def on_chat_updated(self, cid):
        self._pending_reload_chats = True
        if cid == self.cid:
            self._upd.start()
        elif self.isVisible():
            self._upd.start()

    def _live_update(self):
        if self._pending_reload_chats and self.isVisible():
            self._pending_reload_chats = False
            self.reload_chats()
        if self.cid is None or self._filtered() or not self.isVisible():
            return
        at_bottom = self.view.at_bottom()
        n = max(self.PAGE, len(self.view.msgs))
        msgs, more = self.ctx.db.get_messages(self.cid, limit=n)
        if at_bottom:
            self.view.set_messages(msgs, more)
        else:
            bar = self.view.verticalScrollBar()
            pos = bar.value()
            self.view.set_messages(msgs, more, keep_bottom=False)
            QTimer.singleShot(0, lambda: bar.setValue(pos))
        self._update_info()

    def on_media_ready(self, cid, mid):
        if cid == self.cid:
            self.view.refresh_message((cid, mid), self.ctx.db.get_message(cid, mid))

    # ---- действия
    def _fetch_all(self):
        if self.cid is not None:
            self.ctx.worker.fetch_history(self.cid)

    def _fetch_period(self):
        if self.cid is None:
            return
        d = PeriodDialog(self)
        if d.exec():
            a, b = d.range()
            self.ctx.worker.fetch_history(self.cid, a, b)

    def _export(self):
        if self.cid is None:
            return
        chat = self.ctx.db.get_chat(self.cid)
        d = ExportDialog(chat["title"], self)
        if not d.exec():
            return
        fmt = d.fmt
        filters = {"html": "HTML (*.html)", "txt": "Текст (*.txt)", "json": "JSON (*.json)"}
        path = ask_save_path(self, "Сохранить экспорт", f"{safe_name(chat['title'], 60)}.{fmt}", filters[fmt])
        if not path:
            return
        if path.suffix.lower() != "." + fmt:
            path = path.with_suffix("." + fmt)
        d1, d2 = d.dates.range()
        include = d.media.isChecked()
        cid = self.cid
        self.ctx.main.set_task(f"Экспорт «{chat['title']}»…")

        def job():
            return export_chat(self.ctx.db, self.ctx.cipher, self.ctx.data_dir, cid, fmt, path, d1, d2, include)

        def done(n):
            self.ctx.main.set_task(f"Экспорт завершён: {n} сообщений")
            if QMessageBox.question(self, "Экспорт", f"Экспортировано сообщений: {n}\n{path}\n\nОткрыть папку?") \
                    == QMessageBox.Yes:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

        self.ctx.run_bg(job, done)


# ================================================================ Избранное
class FavoritesPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "Избранное", "Важные сообщения, отмеченные звёздочкой. Правый клик — перейти к чату.")
        self.view = MessageList(ctx, show_chat=True,
                                empty_text="Пока ничего нет. Нажмите ☆ у сообщения, чтобы добавить его сюда.")
        self.root.addWidget(self.view, 1)

    def on_show(self):
        self.view.set_messages(self.ctx.db.favorites(), False)


# ================================================================ Медиафайлы
class MediaPage(Page):
    PAGE = 60

    def __init__(self, ctx):
        super().__init__(ctx, "Медиафайлы", "Скачанные вложения из архива. Двойной щелчок — открыть.")
        bar = QHBoxLayout()
        self.kind = QComboBox()
        for text, key in (("Все типы", None), ("Фото", "photo"), ("Видео", "video"),
                          ("Голосовые и аудио", "voice"), ("Документы", "document")):
            self.kind.addItem(text, key)
        self.chat = QComboBox()
        self.chat.setMinimumWidth(220)
        self.kind.currentIndexChanged.connect(self.reload)
        self.chat.currentIndexChanged.connect(self.reload)
        bar.addWidget(self.kind)
        bar.addWidget(self.chat)
        bar.addStretch(1)
        self.count = _lbl("", "Small")
        bar.addWidget(self.count)
        self.root.addLayout(bar)
        self.grid = QListWidget()
        self.grid.setObjectName("MediaGrid")
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setIconSize(QSize(150, 150))
        self.grid.setGridSize(QSize(176, 196))
        self.grid.setWordWrap(True)
        self.grid.setSpacing(6)
        self.grid.setUniformItemSizes(True)
        self.grid.itemDoubleClicked.connect(self._open)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._menu)
        self.root.addWidget(self.grid, 1)
        self.more = _btn("Показать ещё")
        self.more.clicked.connect(lambda: self._load(append=True))
        self.root.addWidget(self.more, 0, Qt.AlignCenter)
        self._items: list = []
        self._queue: list = []
        self._thumb_timer = QTimer(self, interval=15)
        self._thumb_timer.timeout.connect(self._thumb_step)

    def on_show(self):
        cur = self.chat.currentData()
        self.chat.blockSignals(True)
        self.chat.clear()
        self.chat.addItem("Все чаты", None)
        for c in self.ctx.db.archived_chats():
            self.chat.addItem(c["title"], c["id"])
        idx = self.chat.findData(cur)
        self.chat.setCurrentIndex(max(0, idx))
        self.chat.blockSignals(False)
        self.reload()

    def reload(self):
        self.grid.clear()
        self._items.clear()
        self._queue.clear()
        self._load()

    def _load(self, append=False):
        items = self.ctx.db.media_items(self.kind.currentData(), self.chat.currentData(),
                                        limit=self.PAGE, offset=len(self._items))
        for m in items:
            it = QListWidgetItem()
            label = m.media_name or MEDIA_NAMES.get(m.media_type, "Файл")
            if len(label) > 24:
                label = label[:21] + "…"
            it.setText(f"{label}\n{fmt_dt(m.date)}")
            it.setToolTip(f"{m.chat_title}\n{m.media_name or ''}\n{fmt_size(m.media_size)}")
            it.setData(Qt.UserRole, len(self._items))
            if m.media_type in ("photo", "sticker"):
                it.setIcon(icon("image", theme.C["muted"], 48))
                self._queue.append((it, m))
            else:
                it.setIcon(icon(MEDIA_ICONS.get(m.media_type, "file"), theme.C["accent"], 64))
            self._items.append(m)
            self.grid.addItem(it)
        self.more.setVisible(len(items) == self.PAGE)
        self.count.setText(f"Показано: {len(self._items)}")
        if not self._items:
            self.count.setText("Скачанных вложений пока нет. Включите загрузку медиа в настройках.")
        if self._queue:
            self._thumb_timer.start()

    def _thumb_step(self):
        """Миниатюры расшифровываются постепенно, чтобы окно не подвисало."""
        if not self._queue:
            self._thumb_timer.stop()
            return
        it, m = self._queue.pop(0)
        pm = self.ctx.thumb(m, 150, 150)
        if pm is not None:
            try:
                it.setIcon(QIcon(pm))
            except RuntimeError:
                pass

    def _open(self, it):
        self.ctx.open_media(self._items[it.data(Qt.UserRole)])

    def _menu(self, pos):
        it = self.grid.itemAt(pos)
        if it is None:
            return
        m = self._items[it.data(Qt.UserRole)]
        menu = QMenu(self)
        menu.addAction("Открыть", lambda: self.ctx.open_media(m))
        menu.addAction("Сохранить как…", lambda: self.ctx.save_media_as(m, self))
        menu.addAction("Перейти к сообщению", lambda: self.ctx.open_chat(m.chat_id, m.msg_id))
        menu.exec(self.grid.mapToGlobal(pos))


# ==================================================================== Поиск
class SearchPage(Page):
    def __init__(self, ctx):
        super().__init__(ctx, "Поиск", "Поиск по словам и датам во всём архиве. Правый клик по результату — "
                                       "перейти к сообщению в чате.")
        r1 = QHBoxLayout()
        self.q = QLineEdit()
        self.q.setPlaceholderText("Слова, имя отправителя или ссылка…")
        self.q.returnPressed.connect(self.run)
        self.chat = QComboBox()
        self.chat.setMinimumWidth(200)
        go = _btn("Найти", "search", primary=True)
        go.clicked.connect(self.run)
        r1.addWidget(self.q, 1)
        r1.addWidget(self.chat)
        r1.addWidget(go)
        self.root.addLayout(r1)
        r2 = QHBoxLayout()
        self.dates = DateRange()
        self.only_del = QCheckBox("Только удалённые в Telegram")
        self.only_fav = QCheckBox("Только избранное")
        r2.addWidget(self.dates)
        r2.addSpacing(12)
        r2.addWidget(self.only_del)
        r2.addWidget(self.only_fav)
        r2.addStretch(1)
        self.count = _lbl("", "Small")
        r2.addWidget(self.count)
        self.root.addLayout(r2)
        self.view = MessageList(ctx, show_chat=True, empty_text="Введите запрос или выберите период")
        self.root.addWidget(self.view, 1)

    def on_show(self):
        cur = self.chat.currentData()
        self.chat.blockSignals(True)
        self.chat.clear()
        self.chat.addItem("Во всех чатах", None)
        for c in self.ctx.db.archived_chats():
            self.chat.addItem(c["title"], c["id"])
        self.chat.setCurrentIndex(max(0, self.chat.findData(cur)))
        self.chat.blockSignals(False)
        self.q.setFocus()

    def retheme(self):
        if self.view.msgs:
            self.run()

    def run(self):
        d1, d2 = self.dates.range()
        if not self.q.text().strip() and d1 is None and not self.only_del.isChecked() and not self.only_fav.isChecked():
            self.view.empty_text = "Введите запрос или выберите период"
            self.view.set_messages([], False)
            self.count.setText("")
            return
        msgs = self.ctx.db.search(self.q.text(), self.chat.currentData(), d1, d2,
                                  self.only_del.isChecked(), self.only_fav.isChecked(), limit=300)
        self.view.empty_text = "Ничего не найдено"
        self.view.set_messages(msgs, False)
        self.count.setText(f"Найдено: {len(msgs)}" + (" (показаны последние 300)" if len(msgs) >= 300 else ""))


# ================================================================ Настройки
class SettingsPage(QScrollArea):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("SettingsBody")
        self.setWidget(body)
        self.v = QVBoxLayout(body)
        self.v.setContentsMargins(24, 20, 24, 24)
        self.v.setSpacing(14)
        self.v.addWidget(_lbl("Настройки", "H1"))
        s = ctx.settings

        sec = self._section("Аккаунт Telegram")
        self.account = _lbl("", "Muted", True)
        sec.addWidget(self.account)
        row = QHBoxLayout()
        self.login_btn = _btn("Войти", "user", primary=True)
        self.login_btn.clicked.connect(lambda: ctx.main.show_login())
        self.logout_btn = _btn("Выйти из аккаунта", "logout")
        self.logout_btn.setObjectName("Danger")
        self.logout_btn.clicked.connect(self._logout)
        api = _btn("Изменить API ID / Hash")
        api.clicked.connect(self._change_api)
        row.addWidget(self.login_btn)
        row.addWidget(self.logout_btn)
        row.addWidget(api)
        row.addStretch(1)
        sec.addLayout(row)

        sec = self._section("Сохранение сообщений")
        self._toggle(sec, "Автоматическое сохранение новых сообщений",
                     "Новые, изменённые и удалённые сообщения в выбранных чатах записываются сразу.", "autosave")
        self._toggle(sec, "Сразу сохранять новые чаты",
                     "Если вам напишет новый человек, его чат начнёт сохраняться автоматически.", "auto_select_new")
        self._toggle(sec, "Скачивать фотографии", "Фото и стикеры.", "dl_photos", self._media_changed)
        self._toggle(sec, "Скачивать видео", "Видео, видеосообщения и GIF.", "dl_videos", self._media_changed)
        self._toggle(sec, "Сохранять голосовые сообщения", "Голосовые и аудиофайлы.", "dl_voice", self._media_changed)
        self._toggle(sec, "Скачивать документы", "Любые файлы, отправленные как документ.", "dl_docs",
                     self._media_changed)
        r = QHBoxLayout()
        r.addWidget(_lbl("Максимальный размер файла, МБ"))
        r.addStretch(1)
        self.max_mb = QSpinBox()
        self.max_mb.setRange(1, 4000)
        self.max_mb.setValue(int(s.max_file_mb))
        self.max_mb.setFixedWidth(110)
        self.max_mb.valueChanged.connect(lambda val: self._set("max_file_mb", val))
        r.addWidget(self.max_mb)
        sec.addLayout(r)
        r = QHBoxLayout()
        r.addWidget(_lbl("Проверять пропущенное каждые, мин"))
        r.addStretch(1)
        self.interval = QSpinBox()
        self.interval.setRange(1, 240)
        self.interval.setValue(int(s.sync_interval_min))
        self.interval.setFixedWidth(110)
        self.interval.valueChanged.connect(lambda val: self._set("sync_interval_min", val))
        r.addWidget(self.interval)
        sec.addLayout(r)
        rq = _btn("Докачать пропущенные вложения", "download",
                  tip="Поставить в очередь вложения, пропущенные из-за настроек или ошибок")
        rq.clicked.connect(self._requeue)
        sec.addWidget(rq, 0, Qt.AlignLeft)

        sec = self._section("Система")
        self._toggle(sec, "Запускать вместе с Windows", "TeleSave стартует свёрнутым в трей.", "autostart",
                     self._autostart_changed)
        self._toggle(sec, "Работать в системном трее", "При закрытии окна программа продолжает сохранять сообщения.",
                     "tray", lambda v: ctx.main.update_tray())
        self._toggle(sec, "Уведомлять об ошибках синхронизации", "Всплывающие уведомления Windows.", "notify_errors")

        sec = self._section("Оформление")
        r = QHBoxLayout()
        r.addWidget(_lbl("Тема"))
        r.addStretch(1)
        self.theme = QComboBox()
        self.theme.addItem("Тёмная", "dark")
        self.theme.addItem("Светлая", "light")
        self.theme.setCurrentIndex(max(0, self.theme.findData(s.theme)))
        self.theme.currentIndexChanged.connect(lambda: ctx.main.set_theme(self.theme.currentData()))
        r.addWidget(self.theme)
        sec.addLayout(r)

        sec = self._section("Безопасность")
        self.pw_state = _lbl("", "Muted", True)
        sec.addWidget(self.pw_state)
        r = QHBoxLayout()
        self.pw_set = _btn("Установить пароль", "lock")
        self.pw_change = _btn("Сменить пароль", "lock")
        self.pw_remove = _btn("Убрать пароль")
        self.pw_set.clicked.connect(lambda: self._password("set"))
        self.pw_change.clicked.connect(lambda: self._password("change"))
        self.pw_remove.clicked.connect(lambda: self._password("remove"))
        for b in (self.pw_set, self.pw_change, self.pw_remove):
            r.addWidget(b)
        r.addStretch(1)
        sec.addLayout(r)

        sec = self._section("Папка с данными")
        self.dir_lbl = _lbl(str(ctx.data_dir), "Muted", True)
        self.dir_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        sec.addWidget(self.dir_lbl)
        r = QHBoxLayout()
        op = _btn("Открыть папку", "folder")
        op.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(ctx.data_dir))))
        mv = _btn("Перенести в другую папку…")
        mv.clicked.connect(self._move_dir)
        r.addWidget(op)
        r.addWidget(mv)
        r.addStretch(1)
        sec.addLayout(r)

        sec = self._section("Резервные копии")
        sec.addWidget(_lbl("Копия содержит весь зашифрованный архив и вложения. Её можно восстановить "
                           "и на другом компьютере — понадобится пароль копии.", "Muted", True))
        r = QHBoxLayout()
        mk = _btn("Создать резервную копию…", "backup", primary=True)
        mk.clicked.connect(self._backup)
        rs = _btn("Восстановить из копии…", "refresh")
        rs.clicked.connect(self._restore)
        r.addWidget(mk)
        r.addWidget(rs)
        r.addStretch(1)
        sec.addLayout(r)

        sec = self._section("Статистика")
        self.stats = _lbl("", "Muted", True)
        sec.addWidget(self.stats)
        self.v.addStretch(1)

    # ---- построение
    def _section(self, title):
        card = QFrame()
        card.setObjectName("Card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(10)
        lay.addWidget(_lbl(title, "H2"))
        self.v.addWidget(card)
        return lay

    def _toggle(self, lay, title, desc, attr, cb=None):
        row = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(_lbl(title))
        col.addWidget(_lbl(desc, "Small", True))
        row.addLayout(col, 1)
        t = ToggleSwitch()
        t.set_quiet(bool(getattr(self.ctx.settings, attr)))
        t.toggled.connect(lambda val: (self._set(attr, val), cb(val) if cb else None))
        row.addWidget(t)
        lay.addLayout(row)
        return t

    def _set(self, attr, val):
        setattr(self.ctx.settings, attr, val)
        self.ctx.settings.save()

    def on_show(self):
        self.refresh()

    def retheme(self):
        self.refresh()

    def refresh(self):
        w = self.ctx.worker
        logged = w.has_session()
        info = self.ctx.main.account_text if logged and self.ctx.main.account_text else ""
        self.account.setText(("Вы вошли как: " + info) if info else
                             ("Вход выполнен." if logged else "Вход не выполнен. Архив доступен для чтения."))
        self.login_btn.setVisible(not logged)
        self.logout_btn.setVisible(logged)
        has_pw = self.ctx.keystore.has_password()
        self.pw_state.setText("Архив открывается только по паролю." if has_pw else
                              "Пароль не задан: ключ шифрования защищён учётной записью Windows.")
        self.pw_set.setVisible(not has_pw)
        self.pw_change.setVisible(has_pw)
        self.pw_remove.setVisible(has_pw)
        st = self.ctx.db.stats()
        last = self.ctx.db.get_meta("last_sync")
        self.stats.setText(f"Чатов в архиве: {st['chats']} · сообщений: {st['messages']} · "
                           f"удалённых в Telegram: {st['deleted']} · в избранном: {st['favorites']}\n"
                           f"Вложений: {st['media']} ({fmt_size(st['media_bytes']) or '0 Б'}) · "
                           f"последняя синхронизация: {fmt_dt(float(last)) if last else 'ещё не было'}")

    # ---- действия
    def _media_changed(self, _val):
        pass

    def _requeue(self):
        s = self.ctx.settings
        types = []
        if s.dl_photos:
            types += ["photo", "sticker"]
        if s.dl_videos:
            types += ["video", "round", "gif"]
        if s.dl_voice:
            types += ["voice", "audio"]
        if s.dl_docs:
            types += ["document"]
        n = self.ctx.db.requeue_media(types)
        self.ctx.worker.kick_media()
        QMessageBox.information(self, "TeleSave", f"Поставлено в очередь: {n}.\n"
                                "Вложения, удалённые в Telegram, скачать уже нельзя.")

    def _autostart_changed(self, val):
        try:
            if not autostart.set_autostart(val) and sys.platform != "win32":
                QMessageBox.information(self, "TeleSave", "Автозапуск доступен только в Windows.")
        except Exception as e:
            QMessageBox.warning(self, "TeleSave", f"Не удалось изменить автозапуск: {e}")

    def _logout(self):
        if QMessageBox.question(self, "Выход", "Выйти из аккаунта Telegram?\nСохранённый архив останется на месте.") \
                == QMessageBox.Yes:
            self.ctx.worker.logout()

    def _change_api(self):
        if self.ctx.worker.has_session():
            QMessageBox.information(self, "TeleSave", "Сначала выйдите из аккаунта, затем войдите с новыми ключами.")
            return
        self.ctx.db.set_meta("api_id", None)
        self.ctx.db.set_meta("api_hash", None)
        self.ctx.main.show_login()

    def _password(self, mode):
        d = PasswordDialog(self.ctx.keystore, mode, self)
        if d.exec():
            self.ctx.keystore.protect(self.ctx.master, d.new_password)
            QMessageBox.information(self, "TeleSave", "Готово." if mode != "remove" else "Пароль убран.")
            self.refresh()

    def _backup(self):
        d = BackupPasswordDialog(True, self)
        if not d.exec():
            return
        name = "TeleSave_" + dt.datetime.now().strftime("%Y-%m-%d_%H-%M") + ".tsbackup"
        path = ask_save_path(self, "Сохранить резервную копию", name, "Резервная копия TeleSave (*.tsbackup)")
        if not path:
            return
        ctx, pw = self.ctx, d.password
        ctx.main.set_task("Создание резервной копии…")

        def job():
            create_backup(ctx.data_dir, ctx.db.path, ctx.master, pw, path)
            ctx.db.set_meta("last_backup", str(dt.datetime.now().timestamp()))

        ctx.run_bg(job, lambda _r: (ctx.main.set_task("Резервная копия создана"),
                                    QMessageBox.information(self, "TeleSave", f"Резервная копия сохранена:\n{path}")))

    def _restore(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите резервную копию", os.path.expanduser("~"),
                                              "Резервная копия TeleSave (*.tsbackup);;Все файлы (*)")
        if not path:
            return
        d = BackupPasswordDialog(False, self)
        if not d.exec():
            return
        try:
            key = read_backup_key(Path(path), d.password)
        except WrongPassword:
            QMessageBox.warning(self, "TeleSave", "Неверный пароль резервной копии.")
            return
        except Exception as e:
            QMessageBox.warning(self, "TeleSave", f"Не удалось прочитать копию: {e}")
            return
        if QMessageBox.question(self, "Восстановление",
                                "Текущий архив будет заменён содержимым копии (старые данные переместятся в "
                                "папку _before_restore_…). После этого программа перезапустится. Продолжить?") \
                != QMessageBox.Yes:
            return
        ctx = self.ctx
        carry = {k: ctx.db.get_meta(k, True) for k in ("session", "api_id", "api_hash")}
        ctx.main.stop_services()
        try:
            old = restore_backup(Path(path), ctx.data_dir, key, carry)
            # ключ из копии защищаем так же, как был защищён текущий архив
            if ctx.keystore.has_password():
                ctx.keystore.protect(key, d.password)
                note = "Пароль архива теперь совпадает с паролем резервной копии."
            else:
                ctx.keystore.protect(key, None)
                note = ""
            QMessageBox.information(self, "TeleSave", f"Архив восстановлен. Прежние данные: {old.name}\n{note}")
        except Exception as e:
            QMessageBox.critical(self, "TeleSave", f"Ошибка восстановления: {e}")
        ctx.main.restart()

    def _move_dir(self):
        target = QFileDialog.getExistingDirectory(self, "Новая папка для архива", str(self.ctx.data_dir.parent))
        if not target:
            return
        new = Path(target) / "TeleSave"
        if new.resolve() == self.ctx.data_dir.resolve():
            return
        if new.exists() and any(new.iterdir()):
            QMessageBox.warning(self, "TeleSave", f"Папка {new} уже существует и не пуста.")
            return
        if QMessageBox.question(self, "Перенос архива", f"Скопировать архив в\n{new}\nи перезапустить программу?\n"
                                "Старая папка останется — удалите её вручную после проверки.") != QMessageBox.Yes:
            return
        ctx = self.ctx
        ctx.main.stop_services()
        try:
            shutil.copytree(ctx.data_dir, new, ignore=shutil.ignore_patterns("tmp", "logs", "_before_restore_*"))
            from ..paths import set_data_dir
            set_data_dir(new)
        except Exception as e:
            QMessageBox.critical(self, "TeleSave", f"Не удалось перенести: {e}")
        ctx.main.restart()
