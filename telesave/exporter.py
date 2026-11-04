"""Экспорт переписки в TXT / HTML / JSON (расшифрованные копии — сохраняйте их бережно)."""
from __future__ import annotations

import datetime as dt
import html
import json
import re
from pathlib import Path

from .crypto import Cipher
from .db import Database, Msg

MEDIA_LABELS = {"photo": "Фото", "video": "Видео", "round": "Видеосообщение", "gif": "GIF", "voice": "Голосовое",
                "audio": "Аудио", "document": "Документ", "sticker": "Стикер", "geo": "Геопозиция",
                "contact": "Контакт", "poll": "Опрос", "webpage": "Ссылка", "other": "Вложение"}


def safe_name(name: str, limit: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", name or "file").strip(" .") or "file"
    return name[-limit:]


def _fmt(ts) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M:%S") if ts else ""


def _media_line(m: Msg) -> str:
    if not m.media_type:
        return ""
    label = MEDIA_LABELS.get(m.media_type, "Вложение")
    if m.media_type == "geo" and m.extra.get("geo"):
        return f"[{label}: {m.extra['geo'][0]:.5f}, {m.extra['geo'][1]:.5f}]"
    if m.media_type == "contact":
        return f"[{label}: {m.extra.get('contact', '')}]"
    if m.media_type == "poll":
        return f"[{label}: {m.extra.get('poll', '')}]"
    if m.media_type == "webpage":
        return ""
    name = m.media_name or m.extra.get("fname") or ""
    state = {"done": "", "pending": " — не скачано", "skipped": " — не скачано",
             "unavailable": " — недоступно", "error": " — ошибка загрузки"}.get(m.media_status, "")
    return f"[{label}{': ' + name if name else ''}{state}]"


def export_chat(db: Database, cipher: Cipher, data_dir: Path, chat_id: int, fmt: str, out_path: Path,
                date_from=None, date_to=None, include_media=False, progress=None) -> int:
    out_path = Path(out_path)
    chat = db.get_chat(chat_id) or {"title": str(chat_id), "kind": ""}
    title = chat["title"]
    versions = db.versions_map(chat_id)
    files_dir = out_path.with_name(out_path.stem + "_files")
    count = 0

    def export_file(m: Msg) -> str | None:
        if not include_media or m.media_status != "done" or not m.media_path:
            return None
        fname = f"{m.msg_id}_{safe_name(m.media_name or 'file')}"
        try:
            cipher.decrypt_file(Path(data_dir) / m.media_path, files_dir / fname)
            return f"{files_dir.name}/{fname}"
        except Exception:
            return None

    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        if fmt == "txt":
            f.write(f"TeleSave — переписка «{title}»\nЭкспорт: {_fmt(dt.datetime.now().timestamp())}\n")
            f.write("=" * 60 + "\n\n")
        elif fmt == "json":
            f.write('{\n "chat": ' + json.dumps({"id": chat_id, "title": title, "type": chat.get("kind")},
                                                  ensure_ascii=False))
            f.write(',\n "exported_at": ' + json.dumps(dt.datetime.now().isoformat(timespec="seconds")))
            f.write(',\n "messages": [\n')
        else:
            f.write(_HTML_HEAD.replace("{TITLE}", html.escape(title)))

        first = True
        last_day = None
        for m in db.iter_messages(chat_id, date_from, date_to):
            count += 1
            if progress and count % 200 == 0:
                progress(count)
            rel = export_file(m)
            sender = "Вы" if m.out and not m.sender_name else (m.sender_name or "")
            marks = []
            if m.edited:
                marks.append("изменено")
            if m.deleted:
                marks.append("удалено в Telegram")
            if fmt == "txt":
                line = f"[{_fmt(m.date)}] {sender}: "
                body = " ".join(x for x in (_media_line(m), m.text) if x)
                if m.extra.get("fwd"):
                    body = f"(переслано от {m.extra['fwd']}) " + body
                for link in m.extra.get("links", []):
                    body += f" <{link}>"
                if rel:
                    body += f" (файл: {rel})"
                if marks:
                    body += "  {" + ", ".join(marks) + "}"
                f.write(line + body.replace("\n", "\n    ") + "\n")
                for ed, old in versions.get(m.msg_id, []):
                    f.write(f"    · прежняя версия ({_fmt(ed)}): {old}\n")
            elif fmt == "json":
                item = {"id": m.msg_id, "date": dt.datetime.fromtimestamp(m.date).isoformat(), "sender": sender,
                        "sender_id": m.sender_id, "outgoing": m.out, "text": m.text,
                        "media": ({"type": m.media_type, "name": m.media_name, "status": m.media_status, "file": rel}
                                  if m.media_type else None),
                        "edited": m.edited, "deleted_in_telegram": m.deleted, "favorite": m.favorite,
                        "forwarded_from": m.extra.get("fwd"), "links": m.extra.get("links", []),
                        "previous_versions": [{"date": _fmt(ed), "text": t} for ed, t in versions.get(m.msg_id, [])]}
                f.write(("  " if first else ",\n  ") + json.dumps(item, ensure_ascii=False))
            else:
                day = dt.datetime.fromtimestamp(m.date).strftime("%d.%m.%Y")
                if day != last_day:
                    f.write(f'<div class="day"><span>{day}</span></div>\n')
                    last_day = day
                cls = "msg out" if m.out else "msg"
                if m.deleted:
                    cls += " deleted"
                parts = [f'<div class="{cls}"><div class="b">']
                parts.append(f'<div class="s">{html.escape(sender)}</div>')
                if m.extra.get("fwd"):
                    parts.append(f'<div class="fwd">Переслано от {html.escape(m.extra["fwd"])}</div>')
                if rel and m.media_type == "photo":
                    parts.append(f'<a href="{html.escape(rel)}"><img src="{html.escape(rel)}"></a>')
                elif rel:
                    parts.append(f'<a class="file" href="{html.escape(rel)}">📎 {html.escape(m.media_name or "файл")}</a>')
                else:
                    ml = _media_line(m)
                    if ml:
                        parts.append(f'<div class="media">{html.escape(ml)}</div>')
                if m.text:
                    parts.append(f'<div class="t">{_linkify(m.text)}</div>')
                for link in m.extra.get("links", []):
                    parts.append(f'<div class="t">🔗 <a href="{html.escape(link)}">{html.escape(link)}</a></div>')
                for ed, old in versions.get(m.msg_id, []):
                    parts.append(f'<div class="old">Прежняя версия ({_fmt(ed)}): {html.escape(old)}</div>')
                meta = _fmt(m.date)
                if marks:
                    meta = " · ".join(marks) + " · " + meta
                parts.append(f'<div class="m">{html.escape(meta)}</div></div></div>\n')
                f.write("".join(parts))
            first = False

        if fmt == "json":
            f.write("\n ]\n}\n")
        elif fmt == "html":
            f.write("</div></body></html>\n")
    return count


_URL = re.compile(r'(https?://[^\s<>"]+)')


def _linkify(text: str) -> str:
    out = []
    for i, part in enumerate(_URL.split(text)):
        if i % 2:
            out.append(f'<a href="{html.escape(part)}">{html.escape(part)}</a>')
        else:
            out.append(html.escape(part))
    return "".join(out).replace("\n", "<br>")


_HTML_HEAD = """<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<title>{TITLE} — TeleSave</title><style>
body{margin:0;background:#0e1621;color:#e7edf3;font:15px/1.4 "Segoe UI",Arial,sans-serif}
h1{font-size:20px;margin:0;padding:16px 24px;background:#17212b;border-bottom:1px solid #263443}
.wrap{max-width:820px;margin:0 auto;padding:16px}
.day{text-align:center;margin:14px 0}.day span{background:#1e2a36;color:#8b9bab;border-radius:10px;padding:2px 10px;font-size:12px}
.msg{display:flex;margin:4px 0}.msg.out{justify-content:flex-end}
.b{background:#1c2733;border-radius:12px;padding:8px 12px;max-width:70%;word-wrap:break-word}
.out .b{background:#2b5278}.deleted .b{outline:1px solid #e5534b}
.s{color:#64b1f8;font-weight:600;font-size:13px}.fwd{color:#8b9bab;font-style:italic;font-size:13px}
.m{color:#8b9bab;font-size:11px;text-align:right;margin-top:3px}.old{color:#8b9bab;font-size:12px;border-left:2px solid #8b9bab;padding-left:6px;margin-top:4px}
img{max-width:100%;border-radius:8px;margin:4px 0}a{color:#7cc0ff}.media,.file{display:block;margin:4px 0;color:#8b9bab}
</style></head><body><h1>{TITLE}</h1><div class="wrap">
"""
