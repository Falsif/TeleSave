"""Пути приложения: служебная папка, папка архива, ресурсы."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

APP_NAME = "TeleSave"
APP_VERSION = "1.0.0"


def appdata_dir() -> Path:
    """Служебная папка (%APPDATA%\\TeleSave): хранит только путь к архиву."""
    override = os.environ.get("TELESAVE_APPDATA")
    if override:
        p = Path(override)
    elif sys.platform == "win32":
        p = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
    else:
        p = Path.home() / ".config" / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def _bootstrap_file() -> Path:
    return appdata_dir() / "bootstrap.json"


def load_bootstrap() -> dict:
    try:
        return json.loads(_bootstrap_file().read_text("utf-8"))
    except Exception:
        return {}


def save_bootstrap(data: dict) -> None:
    f = _bootstrap_file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, f)


def get_data_dir() -> Path | None:
    p = load_bootstrap().get("data_dir")
    return Path(p) if p else None


def set_data_dir(path: Path | str) -> None:
    d = load_bootstrap()
    d["data_dir"] = str(Path(path))
    save_bootstrap(d)


def default_data_dir() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.exists() else Path.home()) / "TeleSave"


def resource_path(rel: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / rel


def view_temp_dir() -> Path:
    """Временная папка для открытия расшифрованных вложений (очищается при выходе)."""
    p = Path(tempfile.gettempdir()) / "TeleSave_view"
    p.mkdir(parents=True, exist_ok=True)
    return p
