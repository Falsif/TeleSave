"""Настройки программы (settings.json в папке архива, без секретов)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class Settings:
    autosave: bool = True            # автоматически сохранять новые сообщения
    auto_select_new: bool = False    # сразу включать сохранение для новых чатов
    dl_photos: bool = True
    dl_videos: bool = False
    dl_voice: bool = True
    dl_docs: bool = False
    max_file_mb: int = 50
    sync_interval_min: int = 10
    autostart: bool = False
    tray: bool = True
    notify_errors: bool = True
    theme: str = "dark"

    @classmethod
    def load(cls, path: Path) -> "Settings":
        s = cls()
        try:
            data = json.loads(Path(path).read_text("utf-8"))
            names = {f.name: f.type for f in fields(cls)}
            for k, v in data.items():
                if k in names:
                    setattr(s, k, v)
        except Exception:
            pass
        s._path = Path(path)
        return s

    def save(self) -> None:
        p: Path = self._path
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, p)
