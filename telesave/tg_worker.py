"""Работа с Telegram (Telethon) в отдельном потоке с собственным asyncio-циклом.

Интерфейс общается с воркером через публичные методы (они ставят задачи в цикл)
и получает результаты через Qt-сигналы.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import threading
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from telethon import TelegramClient, errors, events, functions, utils
from telethon.sessions import StringSession
from telethon.tl import types

from .crypto import Cipher
from .db import Database
from .paths import APP_VERSION
from .settings import Settings

log = logging.getLogger("telesave.tg")

DOWNLOADABLE = {"photo", "video", "round", "gif", "voice", "audio", "document", "sticker"}
IN_MEMORY_LIMIT = 20 * 1024 * 1024   # файлы меньше — скачиваются сразу в память (без временного файла)
RECENT_WINDOW = 50                   # сколько последних сообщений сверять при синхронизации

ACTIONS = {
    "MessageActionChatAddUser": "добавлен участник",
    "MessageActionChatDeleteUser": "участник удалён или покинул чат",
    "MessageActionChatJoinedByLink": "участник вступил по ссылке",
    "MessageActionChatJoinedByRequest": "участник вступил по заявке",
    "MessageActionChatEditTitle": "изменено название",
    "MessageActionChatEditPhoto": "изменено фото чата",
    "MessageActionChatDeletePhoto": "удалено фото чата",
    "MessageActionChatCreate": "чат создан",
    "MessageActionChannelCreate": "канал создан",
    "MessageActionPinMessage": "закреплено сообщение",
    "MessageActionPhoneCall": "звонок",
    "MessageActionScreenshotTaken": "сделан снимок экрана",
    "MessageActionHistoryClear": "история очищена",
    "MessageActionContactSignUp": "присоединился к Telegram",
    "MessageActionGroupCall": "видеочат",
    "MessageActionSetMessagesTTL": "изменён таймер автоудаления",
}


def describe_action(a) -> str:
    name = type(a).__name__
    text = ACTIONS.get(name, "служебное сообщение")
    if name == "MessageActionChatEditTitle":
        text += f": {a.title}"
    elif name == "MessageActionPhoneCall" and getattr(a, "duration", None):
        text += f", {a.duration // 60}:{a.duration % 60:02d}"
    return text


def chat_kind(e) -> str:
    if isinstance(e, types.User):
        return "bot" if e.bot else "user"
    if isinstance(e, (types.Chat, types.ChatForbidden)):
        return "group"
    if isinstance(e, (types.Channel, types.ChannelForbidden)):
        return "supergroup" if getattr(e, "megagroup", False) else "channel"
    return "user"


def chat_title(e) -> str:
    if isinstance(e, types.User) and e.is_self:
        return "Saved Messages (Избранное Telegram)"
    if isinstance(e, types.User) and e.deleted:
        return "Удалённый аккаунт"
    return utils.get_display_name(e) or "Без названия"


def detect_media(m) -> str | None:
    if m.media is None:
        return None
    if isinstance(m.media, types.MessageMediaWebPage):
        return "webpage"
    if m.photo:
        return "photo"
    if m.voice:
        return "voice"
    if m.video_note:
        return "round"
    if m.gif:
        return "gif"
    if m.video:
        return "video"
    if m.sticker:
        return "sticker"
    if m.audio:
        return "audio"
    if m.document:
        return "document"
    if m.geo:
        return "geo"
    if m.contact:
        return "contact"
    if m.poll:
        return "poll"
    return "other"


def humanize(e: BaseException) -> str:
    if isinstance(e, errors.FloodWaitError):
        return f"Telegram просит подождать {e.seconds} сек. перед следующим запросом"
    table = [
        (errors.PhoneNumberInvalidError, "Неверный номер телефона"),
        (errors.PhoneNumberBannedError, "Этот номер заблокирован в Telegram"),
        (errors.PhoneCodeInvalidError, "Неверный код подтверждения"),
        (errors.PhoneCodeExpiredError, "Код устарел — запросите новый"),
        (errors.PasswordHashInvalidError, "Неверный пароль двухэтапной аутентификации"),
        (errors.ApiIdInvalidError, "Неверные API ID / API Hash"),
        (errors.AuthKeyUnregisteredError, "Сессия завершена — войдите снова"),
        (ConnectionError, "Нет подключения к Telegram"),
        (OSError, "Нет подключения к Telegram"),
        (asyncio.TimeoutError, "Telegram не отвечает (таймаут)"),
    ]
    for cls, text in table:
        if isinstance(e, cls):
            return text
    return str(e) or type(e).__name__


def _ts(d) -> int | None:
    return int(d.timestamp()) if d else None


class TelegramWorker(QObject):
    state_changed = Signal(str, str)        # offline | connecting | online | syncing | logged_out
    login_step = Signal(str, str)           # need_login | code | password | done | error
    dialogs_updated = Signal()
    chat_updated = Signal(object)           # chat_id
    media_ready = Signal(object, object)    # chat_id, msg_id
    sync_finished = Signal(float)
    task_progress = Signal(str)
    task_finished = Signal(str)
    error = Signal(str)
    account_info = Signal(str)

    def __init__(self, db: Database, settings: Settings, cipher: Cipher, data_dir: Path):
        super().__init__()
        self.db, self.settings, self.cipher, self.data_dir = db, settings, cipher, Path(data_dir)
        self.client: TelegramClient | None = None
        self.running = False
        self.online = False
        self.my_name = ""
        self.selected: set[int] = set()
        self.known: set[int] = set()
        self._phone = self._code_hash = None
        self._tasks: list[asyncio.Task] = []
        self._syncing = False
        self._history_running: set[int] = set()
        self._avatar_tried: set[int] = set()
        self._media_event = asyncio.Event()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="telegram", daemon=True)
        self._thread.start()

    # ================================================================ потоки
    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro):
        if self._loop.is_closed():
            coro.close()
            return None
        return asyncio.run_coroutine_threadsafe(self._guard(coro), self._loop)

    async def _guard(self, coro):
        try:
            return await coro
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa
            log.exception("Ошибка в задаче Telegram")
            self.error.emit(humanize(e))

    # ======================================================= публичный API
    def has_credentials(self) -> bool:
        return bool(self.db.get_meta("api_id", True) and self.db.get_meta("api_hash", True))

    def has_session(self) -> bool:
        return bool(self.db.get_meta("session", True))

    def set_credentials(self, api_id: int, api_hash: str):
        self.db.set_meta("api_id", str(int(api_id)), True)
        self.db.set_meta("api_hash", api_hash.strip(), True)
        self._submit(self._drop_client())

    def start(self):
        self._submit(self._start())

    def request_code(self, phone: str):
        self._submit(self._login_call(self._request_code(phone)))

    def submit_code(self, code: str):
        self._submit(self._login_call(self._submit_code(code)))

    def submit_password(self, password: str):
        self._submit(self._login_call(self._submit_password(password)))

    def refresh_dialogs(self):
        self._submit(self._refresh_dialogs())

    def sync_now(self):
        self._submit(self._sync_all())

    def fetch_history(self, chat_id: int, date_from=None, date_to=None):
        self._submit(self._fetch_history(chat_id, date_from, date_to))

    def selection_changed(self, chat_id: int, value: bool):
        self._submit(self._on_selection(chat_id, value))

    def kick_media(self):
        self._loop.call_soon_threadsafe(self._media_event.set)

    def logout(self):
        self._submit(self._logout())

    def shutdown(self, timeout: float = 6.0):
        if self._loop.is_closed() or not self._thread.is_alive() or getattr(self, "_down", False):
            return
        self._down = True
        try:
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop).result(timeout)
        except Exception:
            log.warning("Завершение воркера по таймауту")
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(3)

    # ===================================================== клиент и вход
    async def _make_client(self) -> TelegramClient:
        if self.client is not None:
            return self.client
        api_id = int(self.db.get_meta("api_id", True))
        api_hash = self.db.get_meta("api_hash", True)
        session = self.db.get_meta("session", True) or ""
        self.client = TelegramClient(
            StringSession(session), api_id, api_hash,
            device_model="TeleSave (Windows)", system_version="Windows", app_version=APP_VERSION,
            lang_code="ru", system_lang_code="ru",
            flood_sleep_threshold=120, connection_retries=5, retry_delay=3, auto_reconnect=True,
            request_retries=5,
        )
        self.client.add_event_handler(self._on_new, events.NewMessage())
        self.client.add_event_handler(self._on_edit, events.MessageEdited())
        self.client.add_event_handler(self._on_delete, events.MessageDeleted())
        return self.client

    async def _drop_client(self):
        if self.client is not None and not self.running:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None

    def _save_session(self):
        if self.client is not None:
            try:
                self.db.set_meta("session", self.client.session.save(), True)
            except Exception:
                log.exception("Не удалось сохранить сессию")

    async def _start(self):
        if not self.has_credentials() or not self.has_session():
            self.state_changed.emit("logged_out", "Не выполнен вход в Telegram")
            self.login_step.emit("need_login", "")
            return
        await self._make_client()
        self.state_changed.emit("connecting", "Подключение к Telegram…")
        try:
            await asyncio.wait_for(self.client.connect(), 30)
            if not await self.client.is_user_authorized():
                self.db.set_meta("session", None)
                self.state_changed.emit("logged_out", "Сессия завершена — войдите снова")
                self.login_step.emit("need_login", "Сессия больше не действительна. Войдите снова.")
                await self._drop_client()
                return
        except Exception as e:
            log.warning("Старт без сети: %s", e)
            self.state_changed.emit("offline", "Нет соединения. Архив доступен офлайн.")
        await self._run_main()

    async def _login_call(self, coro):
        try:
            await coro
        except errors.SessionPasswordNeededError:
            hint = ""
            try:
                pwd = await self.client(functions.account.GetPasswordRequest())
                hint = pwd.hint or ""
            except Exception:
                pass
            self.login_step.emit("password", hint)
        except Exception as e:
            log.warning("Ошибка входа: %s", type(e).__name__)
            self.login_step.emit("error", humanize(e))

    async def _request_code(self, phone: str):
        await self._make_client()
        if not self.client.is_connected():
            await asyncio.wait_for(self.client.connect(), 30)
        sent = await self.client.send_code_request(phone)
        self._phone, self._code_hash = phone, sent.phone_code_hash
        if isinstance(sent.type, types.auth.SentCodeTypeApp):
            how = "Код отправлен в приложение Telegram на другом вашем устройстве."
        elif isinstance(sent.type, types.auth.SentCodeTypeSms):
            how = "Код отправлен по SMS."
        else:
            how = "Код отправлен."
        self.login_step.emit("code", how)

    async def _submit_code(self, code: str):
        await self.client.sign_in(phone=self._phone, code=code.strip(), phone_code_hash=self._code_hash)
        await self._after_login()

    async def _submit_password(self, password: str):
        await self.client.sign_in(password=password)
        await self._after_login()

    async def _after_login(self):
        self._save_session()
        self.login_step.emit("done", "")
        await self._run_main()

    async def _logout(self):
        await self._stop_tasks()
        if self.client is not None:
            try:
                await self.client.log_out()
            except Exception:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
        self.client = None
        self.db.set_meta("session", None)
        self.online = False
        self.state_changed.emit("logged_out", "Вы вышли из аккаунта. Архив сохранён.")
        self.account_info.emit("")

    async def _stop_tasks(self):
        self.running = False
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except BaseException:
                pass
        self._tasks.clear()

    async def _shutdown(self):
        await self._stop_tasks()
        if self.client is not None:
            self._save_session()
            try:
                await asyncio.wait_for(self.client.disconnect(), 4)
            except Exception:
                pass

    # ====================================================== основной режим
    async def _run_main(self):
        if self.running:
            return
        self.running = True
        self.online = False
        self.known = set(self.db.chat_ids())
        self.selected = set(self.db.selected_ids())
        self._tasks = [asyncio.create_task(self._supervisor()), asyncio.create_task(self._media_loop())]

    async def _supervisor(self):
        """Следит за соединением: переподключается с паузами и досинхронизирует пропущенное."""
        first, backoff, last_sync = True, 5, 0.0
        while self.running:
            try:
                if not self.client.is_connected():
                    if self.online:
                        self.online = False
                        self.state_changed.emit("offline", "Нет соединения. Переподключение…")
                    try:
                        await asyncio.wait_for(self.client.connect(), 30)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 120)
                        continue
                    backoff = 5
                if not self.online:
                    if not await self.client.is_user_authorized():
                        raise errors.AuthKeyUnregisteredError(None)
                    self.online = True
                    self.state_changed.emit("online", "Подключено")
                    if first:
                        me = await self.client.get_me()
                        self.my_name = utils.get_display_name(me) or "Вы"
                        phone = f" · +{me.phone}" if me.phone else ""
                        self.account_info.emit(f"{self.my_name}{phone}")
                        self._save_session()
                        await self._refresh_dialogs()
                        first = False
                    await self._sync_all()
                    last_sync = time.time()
                elif time.time() - last_sync >= max(1, int(self.settings.sync_interval_min)) * 60:
                    await self._sync_all()
                    last_sync = time.time()
            except asyncio.CancelledError:
                raise
            except (errors.AuthKeyUnregisteredError, errors.UnauthorizedError, errors.SessionRevokedError):
                self.running = False
                self.online = False
                self.db.set_meta("session", None)
                self.client = None
                self.state_changed.emit("logged_out", "Сессия завершена — войдите снова")
                self.login_step.emit("need_login", "Сессия завершена в Telegram. Войдите снова — архив не пострадал.")
                return
            except Exception as e:
                log.warning("Супервизор: %s", e)
                if isinstance(e, (ConnectionError, OSError, asyncio.TimeoutError)):
                    self.online = False
                    self.state_changed.emit("offline", "Нет соединения. Переподключение…")
                else:
                    self.error.emit("Ошибка синхронизации: " + humanize(e))
            await asyncio.sleep(5)

    # ---------------------------------------------------------- события
    async def _register_chat(self, event):
        cid = event.chat_id
        try:
            chat = await event.get_chat()
        except Exception:
            chat = None
        if chat is None:
            return
        sel = bool(self.settings.auto_select_new)
        self.db.ensure_chat(cid, chat_kind(chat), chat_title(chat), getattr(chat, "username", None), sel)
        self.known.add(cid)
        if sel:
            self.selected.add(cid)
        self.dialogs_updated.emit()

    async def _on_new(self, event):
        try:
            cid = event.chat_id
            if cid not in self.known:
                await self._register_chat(event)
            if not self.settings.autosave or cid not in self.selected:
                return
            rec = await self._build_record(event.message, cid)
            _, pending = self.db.save_messages([rec])
            if pending:
                self._media_event.set()
            self.chat_updated.emit(cid)
        except Exception:
            log.exception("Новое сообщение")

    async def _on_edit(self, event):
        try:
            cid, msg = event.chat_id, event.message
            if (self.settings.autosave and cid in self.selected) or self.db.has_message(cid, msg.id):
                rec = await self._build_record(msg, cid)
                _, pending = self.db.save_messages([rec])
                if pending:
                    self._media_event.set()
                self.chat_updated.emit(cid)
        except Exception:
            log.exception("Правка сообщения")

    async def _on_delete(self, event):
        try:
            affected = self.db.mark_deleted(event.chat_id, event.deleted_ids)
            for cid in affected:
                self.chat_updated.emit(cid)
        except Exception:
            log.exception("Удаление сообщения")

    # -------------------------------------------------------- запись
    def _want(self, mtype: str, size: int | None) -> bool:
        s = self.settings
        if size and size > int(s.max_file_mb) * 1024 * 1024:
            return False
        return {"photo": s.dl_photos, "sticker": s.dl_photos, "video": s.dl_videos, "round": s.dl_videos,
                "gif": s.dl_videos, "voice": s.dl_voice, "audio": s.dl_voice, "document": s.dl_docs}.get(mtype, False)

    async def _build_record(self, msg, cid: int) -> dict:
        out = bool(msg.out)
        if out:
            name = self.my_name or "Вы"
        else:
            s = msg.sender
            if s is None and msg.sender_id:
                try:
                    s = await msg.get_sender()
                except Exception:
                    s = None
            name = utils.get_display_name(s) if s else ""
            if getattr(msg, "post_author", None):
                name = msg.post_author
        extra: dict = {}
        service = isinstance(msg, types.MessageService)
        if service:
            text = "[" + describe_action(msg.action) + "]"
            extra["service"] = True
        else:
            text = msg.message or ""
        links = [e.url for e in (getattr(msg, "entities", None) or []) if isinstance(e, types.MessageEntityTextUrl)]
        if links:
            extra["links"] = links
        if getattr(msg, "fwd_from", None):
            fname = None
            try:
                f = msg.forward
                ent = f.sender or f.chat
                fname = utils.get_display_name(ent) if ent else None
            except Exception:
                pass
            extra["fwd"] = fname or getattr(msg.fwd_from, "from_name", None) or "скрытый отправитель"
        rt = getattr(msg, "reply_to", None)
        if rt is not None and getattr(rt, "reply_to_msg_id", None):
            extra["reply_to"] = rt.reply_to_msg_id

        mtype = None if service else detect_media(msg)
        status, size, mname = "none", None, None
        if mtype in DOWNLOADABLE:
            f = msg.file
            if f is not None:
                size = f.size
                mname = f.name
                if f.mime_type:
                    extra["mime"] = f.mime_type
                if getattr(f, "duration", None):
                    extra["duration"] = int(f.duration)
            status = "pending" if self._want(mtype, size) else "skipped"
        elif mtype == "geo" and msg.geo is not None:
            extra["geo"] = [msg.geo.lat, msg.geo.long]
        elif mtype == "contact" and msg.contact is not None:
            c = msg.contact
            extra["contact"] = " ".join(x for x in (c.first_name, c.last_name, c.phone_number) if x)
        elif mtype == "poll" and msg.poll is not None:
            q = msg.poll.poll.question
            extra["poll"] = getattr(q, "text", q)
        elif mtype == "webpage":
            wp = msg.web_preview
            if wp is not None:
                extra["url"] = getattr(wp, "url", None)
                extra["site"] = getattr(wp, "site_name", None) or getattr(wp, "title", None)
        return {"chat_id": cid, "msg_id": msg.id, "date": _ts(msg.date) or int(time.time()),
                "sender_id": msg.sender_id, "sender_name": name, "out": out, "text": text, "extra": extra,
                "media_type": mtype, "media_status": status, "media_size": size, "media_name": mname,
                "edit_date": _ts(getattr(msg, "edit_date", None))}

    # ------------------------------------------------------- диалоги
    async def _refresh_dialogs(self):
        if not self.client or not self.online:
            self.error.emit("Нет подключения к Telegram — список переписок не обновлён")
            return
        self.task_progress.emit("Загрузка списка переписок…")
        rows, entities, order = [], {}, 0
        async for d in self.client.iter_dialogs():
            e = d.entity
            rows.append((d.id, chat_kind(e), chat_title(e), getattr(e, "username", None), order))
            entities[d.id] = e
            order += 1
        self.db.upsert_chats(rows)
        self.known = set(self.db.chat_ids())
        self.selected = set(self.db.selected_ids())
        self.dialogs_updated.emit()
        self.task_finished.emit(f"Переписок: {len(rows)}")
        for cid in list(self.selected):
            if cid in entities:
                await self._load_avatar(cid, entities[cid])
        self.dialogs_updated.emit()

    async def _load_avatar(self, cid: int, entity=None):
        if cid in self._avatar_tried or self.db.has_avatar(cid):
            return
        self._avatar_tried.add(cid)
        try:
            data = await self.client.download_profile_photo(entity or cid, file=bytes, download_big=False)
            if data:
                self.db.set_avatar(cid, data)
        except Exception as e:
            log.debug("Аватар %s: %s", cid, e)

    async def _on_selection(self, cid: int, value: bool):
        if value:
            self.selected.add(cid)
        else:
            self.selected.discard(cid)
        if value and self.running and self.online:
            await self._load_avatar(cid)
            await self._sync_chat(cid)
            self.sync_finished.emit(time.time())

    # ---------------------------------------------------- синхронизация
    async def _flush(self, batch: list) -> None:
        if batch:
            _, pending = self.db.save_messages(batch)
            if pending:
                self._media_event.set()
            batch.clear()

    async def _sync_chat(self, cid: int):
        entity = await self.client.get_input_entity(cid)
        last = self.db.last_msg_id(cid)
        batch: list = []
        if last is None:
            # Первая синхронизация: берём последние 100 сообщений (всю историю — по кнопке)
            for m in await self.client.get_messages(entity, limit=100):
                batch.append(await self._build_record(m, cid))
            await self._flush(batch)
        else:
            # 1) всё новое, что пришло, пока программа была выключена или без сети
            async for m in self.client.iter_messages(entity, min_id=last, reverse=True, wait_time=1):
                batch.append(await self._build_record(m, cid))
                if len(batch) >= 100:
                    await self._flush(batch)
            await self._flush(batch)
            # 2) сверка последних сообщений: правки и удаления, случившиеся офлайн
            recent = [m for m in await self.client.get_messages(entity, limit=RECENT_WINDOW) if m]
            for m in recent:
                batch.append(await self._build_record(m, cid))
            await self._flush(batch)
            if recent:
                ids = {m.id for m in recent}
                gone = [i for i in self.db.live_ids_from(cid, min(ids)) if i not in ids]
                if gone:
                    self.db.mark_deleted(cid, gone)
        self.db.set_chat_synced(cid, time.time())
        self.chat_updated.emit(cid)

    async def _sync_all(self):
        if self._syncing or not self.client or not self.online:
            return
        self._syncing = True
        failed = 0
        try:
            self.state_changed.emit("syncing", "Синхронизация…")
            for cid in list(self.selected):
                if not self.running:
                    break
                try:
                    await self._sync_chat(cid)
                except (ValueError, errors.ChannelPrivateError, errors.ChatForbiddenError):
                    log.info("Чат %s недоступен для синхронизации", cid)
                except errors.FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 1)
                except (ConnectionError, OSError, asyncio.TimeoutError):
                    raise
                except Exception:
                    failed += 1
                    log.exception("Синхронизация чата")
            ts = time.time()
            self.db.set_meta("last_sync", str(ts))
            self.sync_finished.emit(ts)
            if failed:
                self.error.emit(f"Не удалось синхронизировать чатов: {failed}")
        finally:
            self._syncing = False
            if self.online:
                self.state_changed.emit("online", "Подключено")

    async def _fetch_history(self, cid: int, date_from=None, date_to=None):
        if not self.client or not self.online:
            self.error.emit("Нет подключения к Telegram")
            return
        if cid in self._history_running:
            return
        self._history_running.add(cid)
        title = (self.db.get_chat(cid) or {}).get("title", "чат")
        try:
            entity = await self.client.get_input_entity(cid)
            kwargs = {"reverse": True, "wait_time": 1}
            if date_from:
                kwargs["offset_date"] = date_from
            batch, count = [], 0
            self.task_progress.emit(f"«{title}»: загрузка истории…")
            async for m in self.client.iter_messages(entity, **kwargs):
                if date_to and m.date > date_to:
                    break
                batch.append(await self._build_record(m, cid))
                count += 1
                if len(batch) >= 100:
                    await self._flush(batch)
                    self.task_progress.emit(f"«{title}»: обработано {count} сообщений…")
                    self.chat_updated.emit(cid)
            await self._flush(batch)
            if not date_from and not date_to:
                self.db.set_history_done(cid)
            self.chat_updated.emit(cid)
            self.task_finished.emit(f"«{title}»: история сохранена ({count} сообщений)")
        finally:
            self._history_running.discard(cid)

    # ---------------------------------------------------------- медиа
    async def _media_loop(self):
        while self.running:
            try:
                if not self.online:
                    await asyncio.sleep(10)
                    continue
                rows = self.db.pending_media(limit=5)
                if not rows:
                    self._media_event.clear()
                    try:
                        await asyncio.wait_for(self._media_event.wait(), 60)
                    except asyncio.TimeoutError:
                        pass
                    continue
                for r in rows:
                    if not self.running or not self.online:
                        break
                    await self._download_one(r["chat_id"], r["msg_id"], r["media_type"])
            except asyncio.CancelledError:
                raise
            except errors.FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)
            except Exception:
                log.exception("Очередь медиа")
                await asyncio.sleep(10)

    async def _download_one(self, cid: int, mid: int, mtype: str):
        try:
            entity = await self.client.get_input_entity(cid)
            msg = await self.client.get_messages(entity, ids=mid)
            if msg is None or msg.media is None:
                self.db.set_media(cid, mid, "unavailable")   # удалено раньше, чем успели скачать
                self.chat_updated.emit(cid)
                return
            f = msg.file
            ext = (f.ext if f and f.ext else "") or ""
            name = (f.name if f and f.name else None) or f"{mtype}_{mid}{ext}"
            rel = Path("media") / str(cid) / f"{mid}.tsf"
            dst = self.data_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            size = f.size if f and f.size else 0
            if size and size > IN_MEMORY_LIMIT:
                tmp = self.data_dir / "tmp" / uuid.uuid4().hex
                tmp.mkdir(parents=True, exist_ok=True)
                try:
                    path = await self.client.download_media(msg, file=str(tmp))
                    await asyncio.to_thread(self.cipher.encrypt_file, path, dst)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
            else:
                data = await self.client.download_media(msg, file=bytes)
                if data is None:
                    self.db.set_media(cid, mid, "unavailable")
                    return
                size = len(data)
                await asyncio.to_thread(self.cipher.encrypt_bytes_to_file, data, dst)
            self.db.set_media(cid, mid, "done", rel.as_posix(), name, size)
            self.media_ready.emit(cid, mid)
        except (errors.FloodWaitError, ConnectionError, asyncio.CancelledError):
            raise
        except Exception as e:
            log.warning("Медиа %s/%s: %s", cid, mid, e)
            self.db.set_media(cid, mid, "error")
            self.chat_updated.emit(cid)
