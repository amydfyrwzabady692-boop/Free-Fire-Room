from __future__ import annotations

from app.core.config import get_settings


def event_deep_link(token: str) -> str:
    username = get_settings().bot_username.lstrip("@")
    return f"https://t.me/{username}?start=event_{token}"


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
