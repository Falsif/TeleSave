"""Локальное хранилище SQLite. Все тексты, имена и вложения зашифрованы мастер-ключом."""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, field

from .crypto import Cipher

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value BLOB);
CREATE TABLE IF NOT EXISTS chats(
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'user',
    title BLOB, username BLOB, avatar BLOB,
    selected INTEGER NOT NULL DEFAULT 0,
    ord INTEGER NOT NULL DEFAULT 999999,
    last_sync REAL,
    history_done INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages(
    chat_id INTEGER NOT NULL, msg_id INTEGER NOT NULL, date INTEGER NOT NULL,
    sender_id INTEGER, sender_name BLOB, out INTEGER NOT NULL DEFAULT 0,
    text BLOB, extra BLOB, media_type TEXT, media_status TEXT NOT NULL DEFAULT 'none',
    media_path TEXT, media_name BLOB, media_size INTEGER,
    edit_date INTEGER, edited INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0, deleted_at INTEGER,
    favorite INTEGER NOT NULL DEFAULT 0, saved_at INTEGER,
    PRIMARY KEY(chat_id, msg_id)
);
CREATE INDEX IF NOT EXISTS ix_messages_date ON messages(chat_id, date);
CREATE INDEX IF NOT EXISTS ix_messages_gdate ON messages(date);
CREATE INDEX IF NOT EXISTS ix_messages_msgid ON messages(msg_id);
CREATE INDEX IF NOT EXISTS ix_messages_media ON messages(media_status);
CREATE INDEX IF NOT EXISTS ix_messages_fav ON messages(favorite);
CREATE TABLE IF NOT EXISTS versions(
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, msg_id INTEGER NOT NULL,
    text BLOB, edit_date INTEGER, saved_at INTEGER);
CREATE INDEX IF NOT EXISTS ix_versions ON versions(chat_id, msg_id);
"""

# «Помеченные» id каналов/супергрупп меньше этого числа (-100xxxxxxxxxx)
CHANNEL_ID_LIMIT = -1000000000000

MEDIA_GROUPS = {
    "photo": ("photo",),
    "video": ("video", "round", "gif"),
    "voice": ("voice", "audio"),
    "document": ("document", "sticker"),
}


@dataclass
class Msg:
    chat_id: int
    msg_id: int
    date: int
    sender_id: int | None
    sender_name: str
    out: bool
    text: str
    extra: dict
    media_type: str | None
    media_status: str
    media_path: str | None
    media_name: str | None
    media_size: int | None
    edited: bool
    edit_date: int | None
    deleted: bool
    deleted_at: int | None
    favorite: bool
    saved_at: int | None
    chat_title: str = field(default="")


class Database:
    def __init__(self, path, cipher: Cipher):
        self.path = str(path)
        self.c = cipher
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        cn = self.conn()
        cn.executescript(SCHEMA)
        cn.commit()

    # ------------------------------------------------------------ служебное
    def conn(self) -> sqlite3.Connection:
        cn = getattr(self._local, "cn", None)
        if cn is None:
            cn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            cn.row_factory = sqlite3.Row
            cn.execute("PRAGMA journal_mode=WAL")
            cn.execute("PRAGMA synchronous=NORMAL")
            cn.execute("PRAGMA cache_size=-4000")   # ~4 МБ — бережём память
            cn.execute("PRAGMA temp_store=FILE")
            self._local.cn = cn
            with self._lock:
                self._all.append(cn)
        return cn

    def close(self) -> None:
        with self._lock:
            for cn in self._all:
                try:
                    cn.close()
                except Exception:
                    pass
            self._all.clear()
        self._local = threading.local()

    # ---------------------------------------------------------------- meta
    def set_meta(self, key: str, value: str | None, secret: bool = False) -> None:
        cn = self.conn()
        with cn:
            if value is None:
                cn.execute("DELETE FROM meta WHERE key=?", (key,))
            else:
                blob = self.c.enc(value) if secret else value.encode("utf-8")
                cn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, blob))

    def get_meta(self, key: str, secret: bool = False) -> str | None:
        row = self.conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if not row or row[0] is None:
            return None
        return self.c.dec_str(row[0]) if secret else bytes(row[0]).decode("utf-8")

    # --------------------------------------------------------------- chats
    def upsert_chats(self, rows) -> None:
        """rows: (id, kind, title, username, ord)"""
        cn = self.conn()
        with cn:
            for cid, kind, title, username, order in rows:
                cn.execute(
                    "INSERT INTO chats(id,kind,title,username,ord) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, title=excluded.title, "
                    "username=excluded.username, ord=excluded.ord",
                    (cid, kind, self.c.enc(title or ""), self.c.enc(username) if username else None, order))

    def ensure_chat(self, cid: int, kind: str, title: str, username: str | None, selected: bool) -> None:
        cn = self.conn()
        with cn:
            cn.execute("INSERT OR IGNORE INTO chats(id,kind,title,username,selected,ord) VALUES(?,?,?,?,?,-1)",
                       (cid, kind, self.c.enc(title or ""), self.c.enc(username) if username else None, int(selected)))

    def set_avatar(self, cid: int, data: bytes | None) -> None:
        cn = self.conn()
        with cn:
            cn.execute("UPDATE chats SET avatar=? WHERE id=?", (self.c.enc(data) if data else None, cid))

    def has_avatar(self, cid: int) -> bool:
        row = self.conn().execute("SELECT avatar IS NOT NULL FROM chats WHERE id=?", (cid,)).fetchone()
        return bool(row and row[0])

    def set_selected(self, cid: int, value: bool) -> None:
        cn = self.conn()
        with cn:
            cn.execute("UPDATE chats SET selected=? WHERE id=?", (int(value), cid))

    def set_chat_synced(self, cid: int, ts: float) -> None:
        cn = self.conn()
        with cn:
            cn.execute("UPDATE chats SET last_sync=? WHERE id=?", (ts, cid))

    def set_history_done(self, cid: int) -> None:
        cn = self.conn()
        with cn:
            cn.execute("UPDATE chats SET history_done=1 WHERE id=?", (cid,))

    def chat_ids(self) -> list[int]:
        return [r[0] for r in self.conn().execute("SELECT id FROM chats")]

    def selected_ids(self) -> list[int]:
        return [r[0] for r in self.conn().execute("SELECT id FROM chats WHERE selected=1")]

    def _chat_dict(self, r) -> dict:
        d = dict(r)
        d["title"] = self.c.dec_str(r["title"]) or "Без названия"
        d["username"] = self.c.dec_str(r["username"]) if r["username"] is not None else None
        d["avatar"] = self.c.dec(r["avatar"]) if r["avatar"] is not None else None
        return d

    def get_chat(self, cid: int) -> dict | None:
        r = self.conn().execute("SELECT * FROM chats WHERE id=?", (cid,)).fetchone()
        return self._chat_dict(r) if r else None

    def list_chats(self) -> list[dict]:
        rows = self.conn().execute(
            "SELECT c.*, COALESCE(s.cnt,0) AS cnt FROM chats c "
            "LEFT JOIN (SELECT chat_id, COUNT(*) cnt FROM messages GROUP BY chat_id) s ON s.chat_id=c.id "
            "ORDER BY c.ord, c.id").fetchall()
        return [self._chat_dict(r) for r in rows]

    def archived_chats(self) -> list[dict]:
        rows = self.conn().execute(
            "SELECT c.*, s.cnt, s.del, s.last FROM chats c JOIN "
            "(SELECT chat_id, COUNT(*) cnt, SUM(deleted) del, MAX(date) last FROM messages GROUP BY chat_id) s "
            "ON s.chat_id=c.id ORDER BY s.last DESC").fetchall()
        return [self._chat_dict(r) for r in rows]

    def chat_titles(self) -> dict[int, str]:
        return {r[0]: (self.c.dec_str(r[1]) or "Без названия") for r in self.conn().execute("SELECT id,title FROM chats")}

    # ------------------------------------------------------------ messages
    def save_messages(self, recs: list[dict]) -> tuple[int, bool]:
        """Сохраняет записи. Повторы игнорируются, правки сохраняют прошлую версию.
        Возвращает (сколько новых, есть ли вложения в очереди)."""
        cn = self.conn()
        new, pending, now = 0, False, int(time.time())
        with cn:
            for r in recs:
                row = cn.execute("SELECT text, edit_date, date FROM messages WHERE chat_id=? AND msg_id=?",
                                 (r["chat_id"], r["msg_id"])).fetchone()
                if row is None:
                    cn.execute(
                        "INSERT INTO messages(chat_id,msg_id,date,sender_id,sender_name,out,text,extra,media_type,"
                        "media_status,media_name,media_size,edit_date,edited,saved_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (r["chat_id"], r["msg_id"], r["date"], r.get("sender_id"), self.c.enc(r.get("sender_name") or ""),
                         int(r.get("out", False)), self.c.enc(r.get("text") or ""), self.c.enc_json(r.get("extra") or {}),
                         r.get("media_type"), r.get("media_status", "none"),
                         self.c.enc(r["media_name"]) if r.get("media_name") else None, r.get("media_size"),
                         r.get("edit_date"), int(bool(r.get("edit_date"))), now))
                    new += 1
                    pending |= r.get("media_status") == "pending"
                elif r.get("edit_date") and r["edit_date"] != row["edit_date"]:
                    old_text = self.c.dec_str(row["text"]) or ""
                    if old_text != (r.get("text") or ""):
                        cn.execute("INSERT INTO versions(chat_id,msg_id,text,edit_date,saved_at) VALUES(?,?,?,?,?)",
                                   (r["chat_id"], r["msg_id"], row["text"], row["edit_date"] or row["date"], now))
                    cn.execute("UPDATE messages SET text=?, extra=?, edit_date=?, edited=1 WHERE chat_id=? AND msg_id=?",
                               (self.c.enc(r.get("text") or ""), self.c.enc_json(r.get("extra") or {}),
                                r["edit_date"], r["chat_id"], r["msg_id"]))
        return new, pending

    def has_message(self, cid: int, mid: int) -> bool:
        return self.conn().execute("SELECT 1 FROM messages WHERE chat_id=? AND msg_id=?", (cid, mid)).fetchone() is not None

    def mark_deleted(self, chat_id: int | None, ids) -> set[int]:
        """Помечает сообщения «Удалено в Telegram». chat_id=None — личные чаты и обычные группы
        (там id сообщений общие для всего аккаунта)."""
        ids = list(ids)
        affected: set[int] = set()
        now = int(time.time())
        cn = self.conn()
        with cn:
            for i in range(0, len(ids), 400):
                part = ids[i:i + 400]
                q = ",".join("?" * len(part))
                if chat_id is not None:
                    cond, args = f"chat_id=? AND deleted=0 AND msg_id IN ({q})", [chat_id, *part]
                else:
                    cond, args = f"chat_id>? AND deleted=0 AND msg_id IN ({q})", [CHANNEL_ID_LIMIT, *part]
                affected |= {r[0] for r in cn.execute(f"SELECT DISTINCT chat_id FROM messages WHERE {cond}", args)}
                cn.execute(f"UPDATE messages SET deleted=1, deleted_at=? WHERE {cond}", [now, *args])
        return affected

    def last_msg_id(self, cid: int) -> int | None:
        r = self.conn().execute("SELECT MAX(msg_id) FROM messages WHERE chat_id=?", (cid,)).fetchone()
        return r[0] if r and r[0] is not None else None

    def live_ids_from(self, cid: int, min_id: int) -> list[int]:
        return [r[0] for r in self.conn().execute(
            "SELECT msg_id FROM messages WHERE chat_id=? AND msg_id>=? AND deleted=0", (cid, min_id))]

    def _msg(self, r, titles: dict | None = None) -> Msg:
        return Msg(
            chat_id=r["chat_id"], msg_id=r["msg_id"], date=r["date"], sender_id=r["sender_id"],
            sender_name=self.c.dec_str(r["sender_name"]) or "", out=bool(r["out"]),
            text=self.c.dec_str(r["text"]) or "", extra=self.c.dec_json(r["extra"]),
            media_type=r["media_type"], media_status=r["media_status"], media_path=r["media_path"],
            media_name=self.c.dec_str(r["media_name"]) if r["media_name"] is not None else None,
            media_size=r["media_size"], edited=bool(r["edited"]), edit_date=r["edit_date"],
            deleted=bool(r["deleted"]), deleted_at=r["deleted_at"], favorite=bool(r["favorite"]),
            saved_at=r["saved_at"], chat_title=(titles or {}).get(r["chat_id"], ""))

    def get_message(self, cid: int, mid: int) -> Msg | None:
        r = self.conn().execute("SELECT * FROM messages WHERE chat_id=? AND msg_id=?", (cid, mid)).fetchone()
        return self._msg(r, self.chat_titles()) if r else None

    @staticmethod
    def _filters(date_from, date_to, only_deleted, only_fav, sql, args):
        if date_from:
            sql += " AND date>=?"; args.append(int(date_from))
        if date_to:
            sql += " AND date<=?"; args.append(int(date_to))
        if only_deleted:
            sql += " AND deleted=1"
        if only_fav:
            sql += " AND favorite=1"
        return sql, args

    def get_messages(self, cid, before=None, limit=80, date_from=None, date_to=None, only_deleted=False):
        """Страница сообщений (в хронологическом порядке), старше курсора before=(date,msg_id)."""
        sql, args = "SELECT * FROM messages WHERE chat_id=?", [cid]
        if before:
            sql += " AND (date<? OR (date=? AND msg_id<?))"; args += [before[0], before[0], before[1]]
        sql, args = self._filters(date_from, date_to, only_deleted, False, sql, args)
        sql += " ORDER BY date DESC, msg_id DESC LIMIT ?"; args.append(limit + 1)
        rows = self.conn().execute(sql, args).fetchall()
        has_more = len(rows) > limit
        return [self._msg(r) for r in reversed(rows[:limit])], has_more

    def get_messages_after(self, cid, after, limit=300):
        rows = self.conn().execute(
            "SELECT * FROM messages WHERE chat_id=? AND (date>? OR (date=? AND msg_id>?)) "
            "ORDER BY date, msg_id LIMIT ?", (cid, after[0], after[0], after[1], limit)).fetchall()
        return [self._msg(r) for r in rows]

    def iter_messages(self, cid, date_from=None, date_to=None, chunk=500):
        """Все сообщения чата по порядку, порциями (для экспорта)."""
        cur = (-1, -1)
        while True:
            sql, args = "SELECT * FROM messages WHERE chat_id=? AND (date>? OR (date=? AND msg_id>?))", \
                        [cid, cur[0], cur[0], cur[1]]
            sql, args = self._filters(date_from, date_to, False, False, sql, args)
            sql += " ORDER BY date, msg_id LIMIT ?"; args.append(chunk)
            rows = self.conn().execute(sql, args).fetchall()
            if not rows:
                return
            for r in rows:
                yield self._msg(r)
            cur = (rows[-1]["date"], rows[-1]["msg_id"])

    def search(self, query: str, chat_id=None, date_from=None, date_to=None,
               only_deleted=False, only_fav=False, limit=300) -> list[Msg]:
        """Поиск по словам. Текст зашифрован, поэтому фильтруем после расшифровки (потоково)."""
        q = (query or "").casefold().strip()
        sql, args = "SELECT * FROM messages WHERE 1=1", []
        if chat_id is not None:
            sql += " AND chat_id=?"; args.append(chat_id)
        sql, args = self._filters(date_from, date_to, only_deleted, only_fav, sql, args)
        sql += " ORDER BY date DESC, msg_id DESC"
        titles = self.chat_titles()
        out: list[Msg] = []
        for r in self.conn().execute(sql, args):
            m = self._msg(r, titles)
            if q:
                hay = " ".join([m.text, m.sender_name, m.media_name or "", " ".join(m.extra.get("links", [])),
                                m.extra.get("fname", "")]).casefold()
                if q not in hay:
                    continue
            out.append(m)
            if len(out) >= limit:
                break
        out.reverse()
        return out

    def favorites(self, limit=1000) -> list[Msg]:
        return self.search("", only_fav=True, limit=limit)

    def toggle_favorite(self, cid: int, mid: int) -> bool:
        cn = self.conn()
        with cn:
            cn.execute("UPDATE messages SET favorite=1-favorite WHERE chat_id=? AND msg_id=?", (cid, mid))
        r = cn.execute("SELECT favorite FROM messages WHERE chat_id=? AND msg_id=?", (cid, mid)).fetchone()
        return bool(r and r[0])

    def versions(self, cid: int, mid: int) -> list[tuple[int | None, str]]:
        return [(r[0], self.c.dec_str(r[1]) or "") for r in self.conn().execute(
            "SELECT edit_date, text FROM versions WHERE chat_id=? AND msg_id=? ORDER BY id", (cid, mid))]

    def versions_map(self, cid: int) -> dict[int, list]:
        res: dict[int, list] = {}
        for r in self.conn().execute("SELECT msg_id, edit_date, text FROM versions WHERE chat_id=? ORDER BY id", (cid,)):
            res.setdefault(r[0], []).append((r[1], self.c.dec_str(r[2]) or ""))
        return res

    # --------------------------------------------------------------- media
    def pending_media(self, limit=5):
        return self.conn().execute(
            "SELECT chat_id, msg_id, media_type FROM messages WHERE media_status='pending' "
            "ORDER BY date DESC LIMIT ?", (limit,)).fetchall()

    def set_media(self, cid, mid, status, path=None, name=None, size=None) -> None:
        cn = self.conn()
        with cn:
            if status == "done":
                cn.execute("UPDATE messages SET media_status=?, media_path=?, media_name=?, media_size=? "
                           "WHERE chat_id=? AND msg_id=?", (status, path, self.c.enc(name or "file"), size, cid, mid))
            else:
                cn.execute("UPDATE messages SET media_status=? WHERE chat_id=? AND msg_id=?", (status, cid, mid))

    def requeue_media(self, media_types: list[str]) -> int:
        if not media_types:
            return 0
        q = ",".join("?" * len(media_types))
        cn = self.conn()
        with cn:
            cur = cn.execute(f"UPDATE messages SET media_status='pending' WHERE media_status IN ('skipped','error') "
                             f"AND media_type IN ({q})", media_types)
        return cur.rowcount

    def media_items(self, group: str | None = None, chat_id=None, limit=60, offset=0) -> list[Msg]:
        sql, args = "SELECT * FROM messages WHERE media_status='done'", []
        if group and group in MEDIA_GROUPS:
            types = MEDIA_GROUPS[group]
            sql += f" AND media_type IN ({','.join('?' * len(types))})"; args += list(types)
        if chat_id is not None:
            sql += " AND chat_id=?"; args.append(chat_id)
        sql += " ORDER BY date DESC LIMIT ? OFFSET ?"; args += [limit, offset]
        titles = self.chat_titles()
        return [self._msg(r, titles) for r in self.conn().execute(sql, args)]

    # --------------------------------------------------------------- stats
    def stats(self) -> dict:
        cn = self.conn()
        r = cn.execute("SELECT COUNT(*), COALESCE(SUM(deleted),0), COALESCE(SUM(favorite),0), "
                       "COALESCE(SUM(media_status='done'),0), COALESCE(SUM(media_size*(media_status='done')),0) "
                       "FROM messages").fetchone()
        chats = cn.execute("SELECT COUNT(DISTINCT chat_id) FROM messages").fetchone()[0]
        return {"messages": r[0], "deleted": r[1], "favorites": r[2], "media": r[3], "media_bytes": r[4], "chats": chats}
