"""Резервные копии архива (.tsbackup — zip с зашифрованной БД и вложениями).

Мастер-ключ кладётся в копию, зашифрованный отдельным паролем резервной копии,
поэтому копию можно восстановить на другом компьютере.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

from .crypto import Cipher, unwrap_with_password, wrap_with_password
from .db import Database

INFO = "telesave_backup.json"


def create_backup(data_dir: Path, db_path: str, master: bytes, password: str, dest: Path, progress=None) -> None:
    data_dir = Path(data_dir)
    with tempfile.TemporaryDirectory() as td:
        snap = Path(td) / "archive.db"
        src, dst = sqlite3.connect(db_path), sqlite3.connect(snap)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        tmp = Path(str(dest) + ".part")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(INFO, json.dumps({"version": 1, "created": time.time(),
                                         "key": wrap_with_password(master, password)}))
            z.write(snap, "archive.db")
            if (data_dir / "settings.json").exists():
                z.write(data_dir / "settings.json", "settings.json")
            media = data_dir / "media"
            files = [p for p in media.rglob("*.tsf")] if media.exists() else []
            for i, p in enumerate(files, 1):
                z.write(p, p.relative_to(data_dir).as_posix(), compress_type=zipfile.ZIP_STORED)
                if progress and i % 50 == 0:
                    progress(i, len(files))
        Path(tmp).replace(dest)


def read_backup_key(zip_path: Path, password: str) -> bytes:
    with zipfile.ZipFile(zip_path) as z:
        if INFO not in z.namelist() or "archive.db" not in z.namelist():
            raise ValueError("Это не резервная копия TeleSave")
        info = json.loads(z.read(INFO))
    return unwrap_with_password(info["key"], password)   # WrongPassword при ошибке


def restore_backup(zip_path: Path, data_dir: Path, backup_master: bytes, carry: dict) -> Path:
    """Заменяет архив содержимым копии. Старые данные переносятся в _before_restore_<время>.
    carry — секреты текущего входа (session/api_id/api_hash), чтобы не входить заново."""
    data_dir = Path(data_dir)
    old = data_dir / time.strftime("_before_restore_%Y%m%d_%H%M%S")
    old.mkdir(parents=True)
    for name in ("archive.db", "archive.db-wal", "archive.db-shm", "settings.json", "media"):
        p = data_dir / name
        if p.exists():
            shutil.move(str(p), str(old / name))
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename
            pp = PurePosixPath(name)
            if name == INFO or pp.is_absolute() or ".." in pp.parts or info.is_dir():
                continue
            if not (name in ("archive.db", "settings.json") or (pp.parts[0] == "media" and name.endswith(".tsf"))):
                continue
            target = data_dir.joinpath(*pp.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
    db = Database(data_dir / "archive.db", Cipher(backup_master))
    try:
        for k, v in carry.items():
            if v:
                db.set_meta(k, v, True)
    finally:
        db.close()
    return old
