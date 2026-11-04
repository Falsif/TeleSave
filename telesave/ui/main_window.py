"""Главное окно, боковая панель, трей, строка состояния."""
from __future__ import annotations

import time

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                               QMainWindow, QMenu, QPushButton, QStackedWidget, QSystemTrayIcon, QVBoxLayout,
                               QWidget)

from ..paths import resource_path
from . import theme
from .dialogs import LoginDialog
from .icons import icon
from .pages import ArchivePage, ChatsPage, FavoritesPage, MediaPage, SearchPage, SettingsPage
from .widgets import fmt_dt

NAV = [("chats", "Все переписки"), ("archive", "Сохранённые сообщения"), ("star", "Избранное"),
       ("media", "Медиафайлы"), ("search", "Поиск"), ("settings", "Настройки")]


class MainWindow(QMainWindow):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        ctx.main = self
        self.account_text = ""
        self._login_open = False
        self._quitting = False
        self._tray_hint_shown = False
        self._last_notify: dict = {}
        self.restart_requested = False
        self.tray = None
        self.setWindowTitle("TeleSave")
        self.setWindowIcon(QIcon(str(resource_path("assets/icon.png"))))
        self.resize(1200, 780)
        self.setMinimumSize(940, 600)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(256)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(12, 16, 12, 12)
        sv.setSpacing(4)
        logo = QHBoxLayout()
        li = QLabel()
        li.setPixmap(QPixmap(str(resource_path("assets/icon.png"))).scaled(32, 32, Qt.KeepAspectRatio,
                                                                            Qt.SmoothTransformation))
        logo.addWidget(li)
        lt = QLabel("TeleSave")
        lt.setObjectName("Logo")
        logo.addWidget(lt)
        logo.addStretch(1)
        sv.addLayout(logo)
        sv.addSpacing(14)
        self.nav = QButtonGroup(self)
        self.nav_buttons = []
        for i, (ic, text) in enumerate(NAV):
            b = QPushButton("  " + text)
            b.setObjectName("NavButton")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("icon_name", ic)
            self.nav.addButton(b, i)
            self.nav_buttons.append(b)
            sv.addWidget(b)
        self.nav.idClicked.connect(self.go)
        sv.addStretch(1)
        self.acc_lbl = QLabel("")
        self.acc_lbl.setObjectName("Account")
        self.acc_lbl.setWordWrap(True)
        sv.addWidget(self.acc_lbl)
        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("NavButton")
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.clicked.connect(lambda: self.set_theme("light" if theme.CURRENT == "dark" else "dark"))
        sv.addWidget(self.theme_btn)
        root.addWidget(side)

        self.stack = QStackedWidget()
        self.pages = [ChatsPage(ctx), ArchivePage(ctx), FavoritesPage(ctx), MediaPage(ctx), SearchPage(ctx),
                      SettingsPage(ctx)]
        for p in self.pages:
            self.stack.addWidget(p)
        root.addWidget(self.stack, 1)
        self.chats, self.archive, self.favorites, self.media, self.search, self.settings_page = self.pages

        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.dot = QLabel("●")
        self.dot.setObjectName("Dot")
        self.state_lbl = QLabel("Запуск…")
        self.task_lbl = QLabel("")
        self.sync_lbl = QLabel("")
        sb.addWidget(self.dot)
        sb.addWidget(self.state_lbl)
        sb.addWidget(self.task_lbl, 1)
        sb.addPermanentWidget(self.sync_lbl)
        last = ctx.db.get_meta("last_sync")
        self._set_sync(float(last) if last else None)
        self._set_state("connecting", "Запуск…")

        self.update_tray()
        self._retheme_icons()

        w = ctx.worker
        w.state_changed.connect(self._set_state)
        w.login_step.connect(self._on_login_step)
        w.dialogs_updated.connect(self._on_dialogs)
        w.chat_updated.connect(self._on_chat_updated)
        w.media_ready.connect(self._on_media_ready)
        w.sync_finished.connect(self._set_sync)
        w.task_progress.connect(self.set_task)
        w.task_finished.connect(self._task_done)
        w.error.connect(self._on_error)
        w.account_info.connect(self._on_account)

        self.nav_buttons[1 if ctx.db.stats()["messages"] else 0].click()

    # ------------------------------------------------------------ навигация
    def go(self, idx):
        self.nav_buttons[idx].setChecked(True)
        page = self.pages[idx]
        self.stack.setCurrentIndex(idx)
        page.on_show()
        eff = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", page)
        anim.setDuration(170)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(lambda: page.setGraphicsEffect(None))
        anim.start(QPropertyAnimation.DeleteWhenStopped)

    def open_chat(self, cid, mid=None):
        self.nav_buttons[1].setChecked(True)
        self.stack.setCurrentIndex(1)
        self.archive.reload_chats()
        self.archive.open_chat(cid, mid)
        self.show_normal()

    # ------------------------------------------------------------ тема
    def set_theme(self, name):
        self.ctx.settings.theme = name
        self.ctx.settings.save()
        theme.apply(QApplication.instance(), name)
        self._retheme_icons()
        cb = self.settings_page.theme
        cb.blockSignals(True)
        cb.setCurrentIndex(max(0, cb.findData(name)))
        cb.blockSignals(False)
        self.pages[self.stack.currentIndex()].retheme()

    def _retheme_icons(self):
        for b in self.nav_buttons:
            b.setIcon(icon(b.property("icon_name"), theme.C["muted"], 18))
        dark = theme.CURRENT == "dark"
        self.theme_btn.setIcon(icon("sun" if dark else "moon", theme.C["muted"], 18))
        self.theme_btn.setText("  Светлая тема" if dark else "  Тёмная тема")

    # ------------------------------------------------------------ статус
    def _set_state(self, state, text):
        self.dot.setProperty("state", state)
        self.dot.style().unpolish(self.dot)
        self.dot.style().polish(self.dot)
        self.state_lbl.setText(text)
        if self.tray:
            self.tray.setToolTip(f"TeleSave — {text}")
        if state == "logged_out" and self.stack.currentIndex() in (0, 5):
            self.pages[self.stack.currentIndex()].on_show()

    def set_task(self, text):
        self.task_lbl.setText(text)

    def _task_done(self, text):
        self.task_lbl.setText(text)
        QTimer.singleShot(8000, lambda: self.task_lbl.text() == text and self.task_lbl.setText(""))

    def _set_sync(self, ts):
        self.sync_lbl.setText("Последняя синхронизация: " + (fmt_dt(ts) if ts else "ещё не было"))

    def _on_error(self, text):
        self.task_lbl.setText("⚠ " + text)
        now = time.time()
        if self.ctx.settings.notify_errors and self.tray and now - self._last_notify.get(text, 0) > 300:
            self._last_notify[text] = now
            self.tray.showMessage("TeleSave", text, QSystemTrayIcon.Warning, 6000)

    def _on_account(self, text):
        self.account_text = text
        self.acc_lbl.setText(("👤 " + text) if text else "")
        if self.stack.currentIndex() == 5:
            self.settings_page.refresh()

    def _on_dialogs(self):
        if self.stack.currentIndex() == 0:
            self.chats.schedule_reload()

    def _on_chat_updated(self, cid):
        self.archive.on_chat_updated(cid)
        if self.stack.currentIndex() == 0:
            self.chats.schedule_reload()

    def _on_media_ready(self, cid, mid):
        self.archive.on_media_ready(cid, mid)

    # ------------------------------------------------------------ вход
    def _on_login_step(self, step, text):
        if step == "need_login":
            if self.isVisible():
                QTimer.singleShot(300, lambda: self.show_login(text))
            elif self.tray:
                self.tray.showMessage("TeleSave", "Требуется вход в Telegram — откройте программу.",
                                      QSystemTrayIcon.Information, 6000)
        elif step == "done":
            self.chats.on_show()

    def show_login(self, note=""):
        if self._login_open:
            return
        self._login_open = True
        self.show_normal()
        try:
            dlg = LoginDialog(self.ctx, self, note)
            if dlg.exec():
                self.go(0)
                self.set_task("Вход выполнен. Загружаем список переписок…")
        finally:
            self._login_open = False
            if self.stack.currentIndex() == 5:
                self.settings_page.refresh()

    # ------------------------------------------------------------ трей
    def update_tray(self):
        want = self.ctx.settings.tray and QSystemTrayIcon.isSystemTrayAvailable()
        if want and self.tray is None:
            self.tray = QSystemTrayIcon(QIcon(str(resource_path("assets/icon.png"))), self)
            menu = QMenu()
            menu.addAction("Открыть TeleSave", self.show_normal)
            menu.addAction("Синхронизировать сейчас", lambda: self.ctx.worker.sync_now())
            menu.addSeparator()
            menu.addAction("Выход", self.quit_app)
            self.tray.setContextMenu(menu)
            self._tray_menu = menu
            self.tray.activated.connect(lambda r: self.show_normal() if r in (QSystemTrayIcon.Trigger,
                                                                              QSystemTrayIcon.DoubleClick) else None)
            self.tray.setToolTip("TeleSave")
            self.tray.show()
        elif not want and self.tray is not None:
            self.tray.hide()
            self.tray.deleteLater()
            self.tray = None

    def show_normal(self):
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        if not self._quitting and self.tray is not None and self.ctx.settings.tray:
            e.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.showMessage("TeleSave", "Программа продолжает сохранять сообщения в фоне. "
                                      "Выход — через меню значка в трее.", QSystemTrayIcon.Information, 5000)
            return
        self._quitting = True
        e.accept()
        QApplication.instance().quit()

    def quit_app(self):
        self._quitting = True
        if self.tray:
            self.tray.hide()
        QApplication.instance().quit()

    # ------------------------------------------------------------ сервис
    def stop_services(self):
        self.ctx.worker.shutdown()
        self.ctx.db.close()

    def restart(self):
        self.restart_requested = True
        self.quit_app()
