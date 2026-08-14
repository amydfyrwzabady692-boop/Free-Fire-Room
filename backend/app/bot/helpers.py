from __future__ import annotations

from datetime import datetime

from app.core.config import get_settings
from app.core.time import parse_naive_in_tz


def parse_user_datetime(text: str) -> datetime:
    text = (text or "").strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d-%H:%M"):
        try:
            naive = datetime.strptime(text, fmt)
            if naive.year < 1700:
                import jdatetime

                j = jdatetime.datetime(naive.year, naive.month, naive.day, naive.hour, naive.minute)
                g = j.togregorian()
                return parse_naive_in_tz(datetime(g.year, g.month, g.day, g.hour, g.minute), "Asia/Tehran")
            return parse_naive_in_tz(naive, "Asia/Tehran")
        except ValueError:
            continue
    raise ValueError("bad date")


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
