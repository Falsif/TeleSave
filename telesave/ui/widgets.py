"""Общие виджеты: аватары, переключатель, пузыри сообщений, лента сообщений."""
from __future__ import annotations

import datetime as dt
import html
import re

from PySide6.QtCore import (Property, QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDateEdit, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
                               QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from . import theme
from .icons import icon

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
          "октября", "ноября", "декабря"]
KIND_NAMES = {"user": "Личный чат", "bot": "Бот", "group": "Группа", "supergroup": "Супергруппа", "channel": "Канал"}
MEDIA_NAMES = {"photo": "Фото", "video": "Видео", "round": "Видеосообщение", "gif": "GIF", "voice": "Голосовое сообщение",
               "audio": "Аудио", "document": "Документ", "sticker": "Стикер"}
MEDIA_ICONS = {"video": "video", "round": "video", "gif": "video", "voice": "mic", "audio": "music",
               "document": "file", "sticker": "image", "photo": "image"}
AVATAR_COLORS = ["#e17076", "#7bc862", "#e5ca77", "#65aadd", "#a695e7", "#ee7aae", "#6ec9cb", "#faa774"]
URL_RE = re.compile(r'((?:https?://|www\.)[^\s<>"]+|t\.me/[^\s<>"]+)')


# ------------------------------------------------------------------ форматы
def fmt_time(ts) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M") if ts else ""


def fmt_dt(ts) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M") if ts else "—"


def fmt_day(ts) -> str:
    d = dt.datetime.fromtimestamp(ts).date()
    today = dt.date.today()
    if d == today:
        return "Сегодня"
    if d == today - dt.timedelta(days=1):
        return "Вчера"
    s = f"{d.day} {MONTHS[d.month - 1]}"
    return s if d.year == today.year else f"{s} {d.year}"


def fmt_size(n) -> str:
    if not n:
        return ""
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def fmt_ago(ts) -> str:
    if not ts:
        return "ещё не было"
    delta = dt.datetime.now().timestamp() - ts
    if delta < 60:
        return "только что"
    if delta < 3600:
        return f"{int(delta // 60)} мин назад"
    return fmt_dt(ts)


def _soft(s: str, n: int = 28) -> str:
    """Мягкие переносы в очень длинных «словах», чтобы пузырь не растягивался."""
    return re.sub(r"(\S{%d})(?=\S)" % n, "\\1\u200b", s)


def text_to_html(text: str) -> str:
    out = []
    link = theme.C["link"]
    for i, part in enumerate(URL_RE.split(text)):
        if i % 2:
            href = part if part.startswith("http") else "https://" + part
            out.append(f'<a style="color:{link}" href="{html.escape(href, quote=True)}">{html.escape(_soft(part))}</a>')
        else:
            out.append(html.escape(_soft(part)))
    return "".join(out).replace("\n", "<br>")


def initials(title: str) -> str:
    words = [w for w in re.split(r"\s+", title or "") if w and w[0].isalnum()]
    s = "".join(w[0] for w in words[:2]).upper()
    return s or "?"


# ------------------------------------------------------------------ аватар
_AV_CACHE: dict = {}


def avatar_pixmap(data: bytes | None, title: str, key: int, size: int) -> QPixmap:
    ck = (key, size, len(data) if data else 0, title[:3])
    if ck in _AV_CACHE:
        return _AV_CACHE[ck]
    dpr = 2
    pm = QPixmap(size * dpr, size * dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    path = QPainterPath()
    path.addEllipse(0, 0, size * dpr, size * dpr)
    p.setClipPath(path)
    src = QPixmap()
    if data and src.loadFromData(data):
        sc = src.scaled(size * dpr, size * dpr, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        p.drawPixmap((size * dpr - sc.width()) // 2, (size * dpr - sc.height()) // 2, sc)
    else:
        p.fillRect(0, 0, size * dpr, size * dpr, QColor(AVATAR_COLORS[abs(key) % len(AVATAR_COLORS)]))
        p.setPen(QColor("#ffffff"))
        f = QFont()
        f.setBold(True)
        f.setPixelSize(int(size * dpr * 0.38))
        p.setFont(f)
        p.drawText(QRectF(0, 0, size * dpr, size * dpr), Qt.AlignCenter, initials(title))
    p.end()
    pm.setDevicePixelRatio(dpr)
    if len(_AV_CACHE) > 600:
        _AV_CACHE.clear()
    _AV_CACHE[ck] = pm
    return pm


class AvatarLabel(QLabel):
    def __init__(self, size: int = 40, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)

    def set_chat(self, data, title, key):
        self.setPixmap(avatar_pixmap(data, title, key, self._size))


# ------------------------------------------------------------ переключатель
class ToggleSwitch(QCheckBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(44, 24)
        self._knob = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _get(self):
        return self._knob

    def _set(self, v):
        self._knob = v
        self.update()

    knob = Property(float, _get, _set)

    def set_quiet(self, value: bool):
        self.blockSignals(True)
        self.setChecked(value)
        self.blockSignals(False)
        self._knob = 1.0 if value else 0.0
        self.update()

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._knob)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def hitButton(self, pos):
        return self.contentsRect().contains(pos)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        off, on = QColor(theme.C["scroll"]), QColor(theme.C["accent"])
        t = self._knob
        track = QColor(int(off.red() + (on.red() - off.red()) * t), int(off.green() + (on.green() - off.green()) * t),
                       int(off.blue() + (on.blue() - off.blue()) * t))
        if not self.isEnabled():
            track.setAlpha(110)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 2, 44, 20), 10, 10)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(3 + t * 20, 4, 16, 16))
        p.end()


# ------------------------------------------------------------- выбор дат
class DateRange(QWidget):
    changed = Signal()

    def __init__(self, label="Период", parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.chk = QCheckBox(label)
        self.d1, self.d2 = QDateEdit(), QDateEdit()
        today = dt.date.today()
        for d in (self.d1, self.d2):
            d.setCalendarPopup(True)
            d.setDisplayFormat("dd.MM.yyyy")
            d.setEnabled(False)
            d.dateChanged.connect(lambda *_: self.changed.emit())
        self.d1.setDate(today - dt.timedelta(days=30))
        self.d2.setDate(today)
        self.chk.toggled.connect(self._toggle)
        h.addWidget(self.chk)
        h.addWidget(self.d1)
        h.addWidget(QLabel("—"))
        h.addWidget(self.d2)

    def _toggle(self, on):
        self.d1.setEnabled(on)
        self.d2.setEnabled(on)
        self.changed.emit()

    def set_active(self, on: bool):
        self.chk.setChecked(on)

    def range(self):
        """(timestamp_from, timestamp_to) или (None, None)."""
        if not self.chk.isChecked():
            return None, None
        a, b = self.d1.date(), self.d2.date()
        t1 = dt.datetime(a.year(), a.month(), a.day()).timestamp()
        t2 = dt.datetime(b.year(), b.month(), b.day(), 23, 59, 59).timestamp()
        return (min(t1, t2), max(t1, t2))


# -------------------------------------------------------- пузырь сообщения
class MessageBubble(QWidget):
    def __init__(self, ctx, m, show_chat=False, parent=None):
        super().__init__(parent)
        self.ctx, self.m, self.show_chat = ctx, m, show_chat
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        self.bubble = QFrame()
        self.bubble.setObjectName("BubbleOut" if m.out else "BubbleIn")
        self.bubble.setProperty("deleted", bool(m.deleted))
        self.bubble.setMaximumWidth(580)
        self.bubble.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        if m.out:
            row.addStretch(1)
            row.addWidget(self.bubble)
        else:
            row.addWidget(self.bubble)
            row.addStretch(1)
        v = QVBoxLayout(self.bubble)
        v.setContentsMargins(12, 8, 12, 6)
        v.setSpacing(4)

        if show_chat and m.chat_title:
            v.addWidget(self._lbl("ChatRef", "💬 " + m.chat_title))
        if m.sender_name and (not m.out or show_chat) and not (show_chat and m.sender_name == m.chat_title):
            v.addWidget(self._lbl("Sender", m.sender_name))
        if m.extra.get("fwd"):
            v.addWidget(self._lbl("Fwd", "Переслано от " + m.extra["fwd"]))
        media = self._media_widget()
        if media is not None:
            v.addWidget(media)
        if m.text:
            t = QLabel()
            t.setTextFormat(Qt.RichText)
            t.setText(text_to_html(m.text))
            t.setWordWrap(True)
            t.setTextInteractionFlags(Qt.TextBrowserInteraction)
            t.setOpenExternalLinks(True)
            fm = t.fontMetrics()
            longest = max((fm.horizontalAdvance(line) for line in m.text.split("\n")), default=0)
            t.setMinimumWidth(max(40, min(longest + 8, 530)))   # короткие сообщения — в одну строку
            if m.extra.get("service"):
                t.setObjectName("Muted")
            v.addWidget(t)
        for link in m.extra.get("links", [])[:5]:
            ll = QLabel(f'🔗 <a style="color:{theme.C["link"]}" href="{html.escape(link, quote=True)}">'
                        f'{html.escape(_soft(link))}</a>')
            ll.setOpenExternalLinks(True)
            ll.setWordWrap(True)
            v.addWidget(ll)

        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        if m.deleted:
            b = self._lbl("DeletedBadge", "Удалено в Telegram")
            b.setToolTip("Удаление замечено: " + fmt_dt(m.deleted_at))
            bottom.addWidget(b)
        if m.edited:
            e = QPushButton("изменено")
            e.setObjectName("Link")
            e.setCursor(Qt.PointingHandCursor)
            e.setToolTip("Показать предыдущие версии")
            e.clicked.connect(lambda: ctx.show_versions(self.m, self.window()))
            bottom.addWidget(e)
        bottom.addStretch(1)
        self.star = QToolButton()
        self.star.setCursor(Qt.PointingHandCursor)
        self.star.setToolTip("Избранное")
        self.star.clicked.connect(self._toggle_fav)
        self._update_star()
        bottom.addWidget(self.star)
        tm = self._lbl("Meta", fmt_time(m.date))
        tm.setToolTip(f"Отправлено: {fmt_dt(m.date)}\nСохранено: {fmt_dt(m.saved_at)}")
        bottom.addWidget(tm)
        v.addLayout(bottom)

    @staticmethod
    def _lbl(name, text):
        lbl = QLabel(text)
        lbl.setObjectName(name)
        lbl.setWordWrap(name in ("Fwd", "ChatRef", "MediaNote"))
        return lbl

    def _media_widget(self):
        m, ctx = self.m, self.ctx
        t = m.media_type
        if not t:
            return None
        if t == "geo" and m.extra.get("geo"):
            lat, lon = m.extra["geo"]
            lb = QLabel(f'📍 <a style="color:{theme.C["link"]}" href="https://www.openstreetmap.org/?mlat={lat}&mlon={lon}">'
                        f'Геопозиция {lat:.5f}, {lon:.5f}</a>')
            lb.setOpenExternalLinks(True)
            return lb
        if t == "contact":
            return self._lbl("MediaNote", "👤 Контакт: " + m.extra.get("contact", ""))
        if t == "poll":
            return self._lbl("MediaNote", "📊 Опрос: " + m.extra.get("poll", ""))
        if t == "webpage":
            site = m.extra.get("site")
            return self._lbl("MediaNote", "🌐 " + site) if site else None
        if t == "other":
            return self._lbl("MediaNote", "Вложение (тип не поддерживается)")
        name = MEDIA_NAMES.get(t, "Вложение")
        st = m.media_status
        if st == "done":
            if t in ("photo", "sticker"):
                pm = ctx.thumb(m)
                if pm is not None:
                    lb = QLabel()
                    lb.setPixmap(pm)
                    lb.setCursor(Qt.PointingHandCursor)
                    lb.setToolTip("Открыть")
                    lb.mousePressEvent = lambda e: ctx.open_media(m)
                    return lb
            dur = m.extra.get("duration")
            extra = f" · {dur // 60}:{dur % 60:02d}" if dur else ""
            btn = QPushButton(f"  {name}: {m.media_name or ''}{extra} · {fmt_size(m.media_size)}")
            btn.setIcon(icon(MEDIA_ICONS.get(t, "file"), theme.C["accent"]))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("text-align:left")
            btn.clicked.connect(lambda: ctx.open_media(m))
            return btn
        notes = {"pending": f"⏳ {name}: загружается…",
                 "skipped": f"{name}{' «' + m.media_name + '»' if m.media_name else ''} — не скачано "
                            f"(отключено в настройках или превышен размер)",
                 "unavailable": f"{name} — недоступно (удалено до загрузки)",
                 "error": f"{name} — ошибка загрузки"}
        return self._lbl("MediaNote", notes.get(st, name))

    def _update_star(self):
        on = self.m.favorite
        self.star.setIcon(icon("star_fill" if on else "star", theme.C["fav"] if on else theme.C["muted"], 16))

    def _toggle_fav(self):
        self.m.favorite = self.ctx.toggle_fav(self.m)
        self._update_star()

    def set_focus_mark(self, on: bool):
        self.bubble.setProperty("focus", on)
        self.bubble.style().unpolish(self.bubble)
        self.bubble.style().polish(self.bubble)

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        if self.m.text:
            menu.addAction(icon("copy", theme.C["text"]), "Копировать текст",
                           lambda: QGuiApplication.clipboard().setText(self.m.text))
        menu.addAction(icon("star", theme.C["text"]),
                       "Убрать из избранного" if self.m.favorite else "Добавить в избранное", self._toggle_fav)
        if self.m.edited:
            menu.addAction(icon("history", theme.C["text"]), "История изменений",
                           lambda: self.ctx.show_versions(self.m, self.window()))
        if self.m.media_status == "done":
            menu.addAction(icon("folder", theme.C["text"]), "Сохранить вложение как…",
                           lambda: self.ctx.save_media_as(self.m, self.window()))
        if self.show_chat:
            menu.addSeparator()
            menu.addAction(icon("goto", theme.C["text"]), "Перейти к сообщению в чате",
                           lambda: self.ctx.open_chat(self.m.chat_id, self.m.msg_id))
        menu.exec(e.globalPos())


class DaySeparator(QWidget):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 8, 0, 6)
        lbl = QLabel(text)
        lbl.setObjectName("DaySep")
        h.addStretch(1)
        h.addWidget(lbl)
        h.addStretch(1)


# ----------------------------------------------------------- лента сообщений
class MessageList(QScrollArea):
    load_more = Signal()

    def __init__(self, ctx, show_chat=False, empty_text="Здесь пока нет сохранённых сообщений", parent=None):
        super().__init__(parent)
        self.ctx, self.show_chat, self.empty_text = ctx, show_chat, empty_text
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._c = QWidget()
        self._c.setObjectName("MsgArea")
        self._v = QVBoxLayout(self._c)
        self._v.setContentsMargins(18, 10, 18, 14)
        self._v.setSpacing(1)
        self.setWidget(self._c)
        self.msgs: list = []
        self.has_more = False
        self._widgets: dict = {}

    def _clear(self):
        while self._v.count():
            it = self._v.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self._widgets.clear()

    def _build(self):
        self._c.setUpdatesEnabled(False)
        self._clear()
        self._v.addStretch(1)
        if self.has_more:
            b = QPushButton("Показать более ранние сообщения")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(self.load_more.emit)
            box = QHBoxLayout()
            box.addStretch(1)
            box.addWidget(b)
            box.addStretch(1)
            wrap = QWidget()
            wrap.setLayout(box)
            self._v.addWidget(wrap)
        if not self.msgs:
            e = QLabel(self.empty_text)
            e.setObjectName("Muted")
            e.setAlignment(Qt.AlignCenter)
            e.setWordWrap(True)
            self._v.addWidget(e)
            self._v.addStretch(1)
        last_day = None
        for m in self.msgs:
            day = dt.datetime.fromtimestamp(m.date).date()
            if day != last_day:
                self._v.addWidget(DaySeparator(fmt_day(m.date)))
                last_day = day
            w = MessageBubble(self.ctx, m, self.show_chat)
            self._widgets[(m.chat_id, m.msg_id)] = w
            self._v.addWidget(w)
        self._c.setUpdatesEnabled(True)

    def set_messages(self, msgs, has_more=False, focus=None, keep_bottom=True):
        self.msgs, self.has_more = list(msgs), has_more
        self._build()
        if focus and focus in self._widgets:
            QTimer.singleShot(60, lambda: self._focus(focus))
        elif keep_bottom:
            QTimer.singleShot(0, self.scroll_bottom)
            QTimer.singleShot(80, self.scroll_bottom)

    def prepend(self, older, has_more):
        bar = self.verticalScrollBar()
        dist = bar.maximum() - bar.value()
        self.msgs = list(older) + self.msgs
        self.has_more = has_more
        self._build()
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum() - dist))
        QTimer.singleShot(80, lambda: bar.setValue(bar.maximum() - dist))

    def at_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.maximum() - bar.value() < 60

    def scroll_bottom(self):
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _focus(self, key):
        w = self._widgets.get(key)
        if w is None:
            return
        self.ensureWidgetVisible(w, 0, 120)
        w.set_focus_mark(True)
        QTimer.singleShot(2500, lambda: w.set_focus_mark(False) if key in self._widgets else None)

    def refresh_message(self, key, msg):
        """Перерисовать одно сообщение (например, когда докачалось вложение)."""
        w = self._widgets.get(key)
        if w is None or msg is None:
            return
        idx = self._v.indexOf(w)
        nw = MessageBubble(self.ctx, msg, self.show_chat)
        self._v.insertWidget(idx, nw)
        self._v.removeWidget(w)
        w.deleteLater()
        self._widgets[key] = nw
        for i, m in enumerate(self.msgs):
            if (m.chat_id, m.msg_id) == key:
                self.msgs[i] = msg
                break
