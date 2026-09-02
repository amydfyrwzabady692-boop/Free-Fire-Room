from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import selectinload

from app.bot.helpers import esc
from app.core.time import format_local
from app.models.channel import Channel
from app.models.event import Event, EventRequiredChannel
from app.models.organizer import Organizer
from app.models.user import User


def _loaded(event: Event, name: str):
    """Read a relationship only if it is already loaded.

    These helpers render cards from both sync and async sessions. Touching an
    unloaded relationship on an AsyncSession raises MissingGreenlet, which would
    take down a whole panel view over a cosmetic field, so callers that want the
    channel must eager-load it via event_public_load_options().
    """
    from sqlalchemy import inspect as _inspect

    try:
        if name in _inspect(event).unloaded:
            return None
    except Exception:  # noqa: BLE001 - a detached or plain object: just try it
        pass
    return getattr(event, name, None)


def event_public_load_options() -> tuple:
    return (
        selectinload(Event.organizer).selectinload(Organizer.user),
        selectinload(Event.channel),
        selectinload(Event.prizes),
        selectinload(Event.required_channels).selectinload(EventRequiredChannel.channel),
    )


def resolve_event_channel(event: Event) -> Channel | None:
    channel = _loaded(event, "channel")
    if channel:
        return channel
    for link in _loaded(event, "required_channels") or []:
        if link.is_active and link.channel:
            return link.channel
    return None


def organizer_public_name(organizer: Organizer | None, user: User | None = None) -> str:
    linked = user
    if linked is None and organizer is not None:
        linked = _loaded(organizer, "user")
    if organizer and (organizer.display_name or "").strip():
        name = organizer.display_name.strip()
    elif linked and (linked.first_name or "").strip():
        name = linked.first_name.strip()
    elif linked and linked.username:
        return f"@{linked.username.lstrip('@')}"
    else:
        name = "برگزارکننده"
    if linked and linked.username:
        return f"{name} (@{linked.username.lstrip('@')})"
    return name


def channel_public_label(channel: Channel | None) -> str:
    if not channel:
        return "—"
    title = (channel.title or "").strip() or "—"
    username = (channel.username or "").strip().lstrip("@")
    if username:
        return f"{title} (@{username})"
    return title


AUTO_TITLE_PREFIX = "کاستوم "
GENERIC_TITLES = {"کاستوم جایزه‌دار", "کاستوم", ""}


def default_custom_description(
    *,
    custom_description: str | None = None,
    title: str | None = None,
    channel_title: str | None = None,
) -> str | None:
    """What to store as the custom's description.

    Returns None when the organizer skipped the step and the only thing we
    could invent is the auto-generated title ("کاستوم <channel>"), which just
    repeats the channel line on the card.
    """
    desc = (custom_description or "").strip()
    if desc:
        return desc[:500]
    auto_title = (title or "").strip()
    ch = (channel_title or "").strip()
    if auto_title and auto_title not in GENERIC_TITLES:
        if ch and auto_title == f"{AUTO_TITLE_PREFIX}{ch}":
            return None
        return auto_title[:500]
    return None


def event_about_text(event: Event) -> str:
    """Free-text description, or "" when there is nothing worth showing."""
    desc = (event.description or "").strip()
    prize = (event.prize_summary or "").strip()
    if desc and desc != prize:
        title = (event.title or "").strip()
        ch = resolve_event_channel(event)
        ch_title = (ch.title or "").strip() if ch else ""
        if ch_title and desc in {f"{AUTO_TITLE_PREFIX}{ch_title}", ch_title}:
            return ""
        if desc == title and title in GENERIC_TITLES:
            return ""
        return desc
    title = (event.title or "").strip()
    if title in GENERIC_TITLES:
        return ""
    ch = resolve_event_channel(event)
    ch_title = (ch.title or "").strip() if ch else ""
    if ch_title and title == f"{AUTO_TITLE_PREFIX}{ch_title}":
        return ""
    return title


def event_prize_text(event: Event) -> str:
    prize = (event.prize_summary or "").strip()
    if not prize:
        rows = _loaded(event, "prizes") or []
        prize = "\n".join(f"{p.place}. {p.title}" for p in rows if p.title)
    return prize or "اعلام نشده"


def _one_line(value: str, limit: int) -> str:
    flat = " ".join((value or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"


#: Telegram truncates inline button text around 64 characters, and the Jalali
#: stamp alone is ~20, so the prize gets what is left rather than the label
#: silently losing its tail.
LIST_LABEL_LIMIT = 64


def format_event_list_label(event: Event) -> str:
    """Button label. The prize is why anyone taps, so it always shows."""
    prize_text = event_prize_text(event)
    if event.starts_at < datetime.now(UTC):
        head = "گذشته"
    else:
        head = f"🕐 {format_local(event.starts_at, event.timezone, compact=True)}"
    budget = LIST_LABEL_LIMIT - len(head) - len(" · 🎁 ")
    return f"{head} · 🎁 {_one_line(prize_text, max(8, budget))}"


def format_time_left(starts_at: datetime, now: datetime | None = None) -> str:
    """"مانده: ۲ روز و ۳ ساعت" instead of "مانده: 4380 دقیقه"."""
    now = now or datetime.now(UTC)
    total = int((starts_at - now).total_seconds())
    if total <= 0:
        return "ساعت کاستوم رسیده"
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"مانده: {days} روز و {hours} ساعت" if hours else f"مانده: {days} روز"
    if hours:
        return f"مانده: {hours} ساعت و {minutes} دقیقه" if minutes else f"مانده: {hours} ساعت"
    return f"مانده: {max(1, minutes)} دقیقه"


def format_capacity_line(event: Event) -> str:
    capacity = int(event.capacity or 0)
    taken = max(0, int(event.confirmed_count or 0))
    if capacity <= 0:
        return f"👥 ثبت‌نام قطعی: {taken}"
    free = max(0, capacity - taken)
    if free == 0:
        return f"👥 ظرفیت: {taken}/{capacity} — تکمیل"
    return f"👥 ظرفیت: {taken}/{capacity} — {free} جای خالی"


def required_channel_count(event: Event) -> int:
    return len([c for c in (event.required_channels or []) if c.is_active])


def format_event_identity_block(event: Event) -> str:
    org = organizer_public_name(event.organizer)
    ch = channel_public_label(resolve_event_channel(event))
    about = event_about_text(event)
    prize = event_prize_text(event)
    lines = [
        f"👤 <b>برگزارکننده:</b> {esc(org)}",
        f"📢 <b>کانال:</b> {esc(ch)}",
    ]
    if about:
        lines.append(f"📝 <b>درباره کاستوم</b>\n{esc(about)}")
    lines.append("━━━━━━━━━━━━━━")
    lines.append(f"💎 <b>جایزه</b>\n{esc(prize)}")
    return "\n".join(lines)
