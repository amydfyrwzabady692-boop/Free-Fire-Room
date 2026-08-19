from __future__ import annotations

import html

from aiogram.types import CallbackQuery

from app.core.config import get_settings
from app.core.logging import get_logger

_log = get_logger(__name__)


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=False)


def event_deep_link(token: str) -> str:
    username = get_settings().bot_username.lstrip("@")
    return f"https://t.me/{username}?start=event_{token}"


def add_bot_to_channel_url() -> str | None:
    username = get_settings().bot_username.lstrip("@")
    if not username:
        return None
    return f"https://t.me/{username}?startchannel=true&admin=invite_users"


def parse_channel_ref_text(text: str) -> str | int | None:
    raw = (text or "").strip()
    if not raw or raw in {"-", "—"}:
        return None
    lower = raw.lower()
    for prefix in ("https://", "http://"):
        if lower.startswith(prefix):
            raw = raw[len(prefix) :]
            lower = raw.lower()
            break
    if lower.startswith("www."):
        raw = raw[4:]
        lower = raw.lower()
    if lower.startswith("t.me/"):
        path = raw.split("/", 1)[1]
        path = path.split("?")[0].strip("/")
        first = path.split("/")[0]
        if first.startswith("+"):
            return f"https://t.me/{first}"
        if first.lower() == "joinchat":
            code = path.split("/", 1)[-1]
            return f"https://t.me/joinchat/{code}"
        if first.lstrip("-").isdigit():
            return int(first)
        handle = first.lstrip("@")
        return f"@{handle}" if handle else None
    if raw.lstrip("-").isdigit():
        return int(raw)
    if " " in raw:
        return None
    handle = raw.lstrip("@")
    return f"@{handle}" if handle else None


def extract_channel_ref(message) -> str | int | None:
    chat = getattr(message, "forward_from_chat", None)
    origin = getattr(message, "forward_origin", None)
    if chat is None and origin is not None:
        chat = getattr(origin, "chat", None)
    if chat is not None and getattr(chat, "type", None) in {"channel", "supergroup"}:
        return chat.id
    return parse_channel_ref_text(getattr(message, "text", None) or getattr(message, "caption", None) or "")


def normalize_join_url(raw: str) -> tuple[str, str] | None:
    text = (raw or "").strip()
    if not text or text == "-":
        return None
    if text.startswith("http://") or text.startswith("https://"):
        label = text.split("/")[-1][:32] or "عضویت"
        return label, text
    handle = text.lstrip("@")
    if handle.startswith("t.me/"):
        handle = handle.split("t.me/", 1)[1]
    handle = handle.split("?")[0].strip("/")
    if not handle or " " in handle:
        return None
    return handle, f"https://t.me/{handle}"


async def ack_callback(cb: CallbackQuery, text: str | None = None, **kwargs) -> None:
    try:
        await cb.answer("" if text is None else text, **kwargs)
    except Exception:
        _log.exception("callback_ack_failed")


async def reply_callback(cb: CallbackQuery, text: str, **kwargs) -> None:
    msg = cb.message
    if msg is not None and hasattr(msg, "answer"):
        try:
            await msg.answer(text, **kwargs)
            return
        except Exception:
            _log.exception("callback_message_reply_failed")
    try:
        await cb.bot.send_message(cb.from_user.id, text, **kwargs)
    except Exception:
        _log.exception("callback_dm_reply_failed")
