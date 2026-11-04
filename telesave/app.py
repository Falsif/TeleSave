"""Точка входа: первый запуск, разблокировка архива, запуск окна и Telegram-воркера."""
from __future__ import annotations

import argparse
import getpass
import logging
import os
import shutil
import sys
import threading
import time
from collections import OrderedDict
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from .crypto import Cipher, KeyStore, KeyUnavailable
from .db import Database
from .exporter import safe_name
from .paths import APP_NAME, appdata_dir, get_data_dir, resource_path, set_data_dir, view_temp_dir
from .settings import Settings

log = logging.getLogger("telesave")


class AppContext(QObject):
    """Общие объекты и действия, нужные разным частям интерфейса."""
    _bg_done = Signal(object, object, object)   # callback, result, error

    def __init__(self, data_dir: Path, settings: Settings, cipher: Cipher, db: Database,
                 keystore: KeyStore, master: bytes):
        super().__init__()
        self.data_dir, self.settings, self.cipher, self.db = data_dir, settings, cipher, db
        self.keystore, self.master = keystore, master
        self.worker = None
        self.main = None
        self._thumbs: OrderedDict = OrderedDict()
        self._bg_done.connect(self._deliver, Qt.QueuedConnection)

    # ---- фоновые задачи (экспорт, бэкап)
    def run_bg(self, fn, on_done=None):
        def target():
            try:
                res = fn()
                self._bg_done.emit(on_done, res, None)
            except Exception as e:  # noqa
                log.exception("Фоновая задача")
                self._bg_done.emit(on_done, None, e)
        threading.Thread(target=target, daemon=True).start()

    def _deliver(self, cb, res, err):
        if err is not None:
            QMessageBox.warning(self.main, "TeleSave", f"Ошибка: {err}")
            if self.main:
                self.main.set_task("")
        elif cb:
            cb(res)

    # ---- медиа
    def thumb(self, m, max_w=320, max_h=300):
        key = (m.chat_id, m.msg_id, max_w, max_h)
        if key in self._thumbs:
            self._thumbs.move_to_end(key)
            return self._thumbs[key]
        try:
            data = self.cipher.decrypt_file_to_bytes(self.data_dir / m.media_path)
        except Exception:
            return None
        pm = QPixmap()
        if not pm.loadFromData(data):
            return None
        pm = pm.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._thumbs[key] = pm
        while len(self._thumbs) > 120:
            self._thumbs.popitem(last=False)
        return pm

    def _decrypted_copy(self, m) -> Path:
        dst = view_temp_dir() / f"{abs(m.chat_id)}_{m.msg_id}_{safe_name(m.media_name or 'file')}"
        if not dst.exists():
            self.cipher.decrypt_file(self.data_dir / m.media_path, dst)
        return dst

    def open_media(self, m):
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._decrypted_copy(m))))
        except Exception as e:
            QMessageBox.warning(self.main, "TeleSave", f"Не удалось открыть вложение: {e}")

    def save_media_as(self, m, parent):
        path, _ = QFileDialog.getSaveFileName(parent, "Сохранить вложение",
                                              str(Path.home() / safe_name(m.media_name or "file")))
        if path:
            try:
                self.cipher.decrypt_file(self.data_dir / m.media_path, path)
            except Exception as e:
                QMessageBox.warning(parent, "TeleSave", f"Ошибка: {e}")

    # ---- сообщения и чаты
    def toggle_fav(self, m) -> bool:
        return self.db.toggle_favorite(m.chat_id, m.msg_id)

    def show_versions(self, m, parent):
        from .ui.dialogs import VersionsDialog
        VersionsDialog(self.db.versions(m.chat_id, m.msg_id), m.text, parent).exec()

    def set_selected(self, cid, value):
        self.db.set_selected(cid, value)
        self.worker.selection_changed(cid, value)

    def open_chat(self, cid, mid=None):
        self.main.open_chat(cid, mid)


# ======================================================================= запуск
SERVER_NAME = f"TeleSave-{getpass.getuser()}"


def _single_instance(wait_previous: bool) -> QLocalServer | None:
    """Вторая копия программы просто показывает окно первой."""
    for attempt in range(40 if wait_previous else 1):
        sock = QLocalSocket()
        sock.connectToServer(SERVER_NAME)
        if sock.waitForConnected(300):
            if wait_previous:
                sock.disconnectFromServer()
                time.sleep(0.3)
                continue
            sock.write(b"show")
            sock.flush()
            sock.waitForBytesWritten(500)
            return None
        break
    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer()
    server.listen(SERVER_NAME)
    return server


def _setup_logging(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(folder / "telesave.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(logging.INFO)
    logging.getLogger("telethon").setLevel(logging.WARNING)


def _open_archive(app) -> tuple[Path, KeyStore, bytes] | None:
    from .ui.dialogs import SetupDialog, UnlockDialog
    data_dir = get_data_dir()
    while True:
        if data_dir is None or not KeyStore(data_dir).exists():
            dlg = SetupDialog()
            if not dlg.exec():
                return None
            data_dir = dlg.data_dir
            ks = KeyStore(data_dir)
            if not ks.exists():
                master = ks.create(dlg.password)
                set_data_dir(data_dir)
                return data_dir, ks, master
            continue
        ks = KeyStore(data_dir)
        if ks.has_password():
            dlg = UnlockDialog(ks)
            r = dlg.exec()
            if r == 2:
                data_dir = None
                continue
            if not r:
                return None
            master = dlg.master
        else:
            try:
                master = ks.unlock()
            except KeyUnavailable:
                b = QMessageBox.warning(
                    None, APP_NAME,
                    f"Не удалось расшифровать ключ архива в папке\n{data_dir}\n\n"
                    "Скорее всего архив перенесён с другого компьютера или из другой учётной записи Windows. "
                    "Используйте восстановление из резервной копии.\n\nВыбрать другую папку?",
                    QMessageBox.Yes | QMessageBox.No)
                if b != QMessageBox.Yes:
                    return None
                data_dir = None
                continue
        set_data_dir(data_dir)
        return data_dir, ks, master


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minimized", action="store_true")
    parser.add_argument("--restarted", action="store_true")
    args, _ = parser.parse_known_args()

    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LUNEXA.TeleSave")
        except Exception:
            pass

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(str(resource_path("assets/icon.png"))))
    f = QFont("Segoe UI", 10)
    app.setFont(f)
    _setup_logging(appdata_dir() / "logs")

    server = _single_instance(args.restarted)
    if server is None:
        return 0

    from .ui import theme
    theme.apply(app, "dark")

    opened = _open_archive(app)
    if opened is None:
        return 0
    data_dir, ks, master = opened
    _setup_logging(data_dir / "logs")
    settings = Settings.load(data_dir / "settings.json")
    theme.apply(app, settings.theme)
    cipher = Cipher(master)
    db = Database(data_dir / "archive.db", cipher)
    shutil.rmtree(data_dir / "tmp", ignore_errors=True)

    from .tg_worker import TelegramWorker
    from .ui.main_window import MainWindow
    ctx = AppContext(data_dir, settings, cipher, db, ks, master)
    worker = TelegramWorker(db, settings, cipher, data_dir)
    ctx.worker = worker
    win = MainWindow(ctx)

    def on_conn():
        s = server.nextPendingConnection()
        if s is not None:
            s.waitForReadyRead(300)
            win.show_normal()
    server.newConnection.connect(on_conn)

    if not (args.minimized and settings.tray and win.tray is not None):
        win.show()
    worker.start()
    log.info("TeleSave запущен, папка данных выбрана")
    rc = app.exec()

    try:
        worker.shutdown()
    except Exception:
        pass
    try:
        db.close()
    except Exception:
        pass
    shutil.rmtree(view_temp_dir(), ignore_errors=True)
    server.close()
    if win.restart_requested:
        if getattr(sys, "frozen", False):
            QProcess.startDetached(sys.executable, ["--restarted"])
        else:
            QProcess.startDetached(sys.executable, [os.path.abspath(sys.argv[0]), "--restarted"])
    return rc
