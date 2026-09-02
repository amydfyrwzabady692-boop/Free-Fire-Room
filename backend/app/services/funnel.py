"""Per-custom funnel: saw the card -> started -> qualified -> got the room.

Every number except the first already existed in the database and nothing
surfaced it. For an organizer this is the difference between "12 people showed
up" and "40 opened the link, 18 started, 12 qualified, 12 got the room" -
which tells them *where* they are losing people.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import DeliveryStatus, RegistrationStatus
from app.models.analytics import EventView
from app.models.jobs import Delivery
from app.models.registration import Registration


async def record_view(db: AsyncSession, event_id, user_id, source: str | None = None) -> None:
    """Remember that this person saw this custom. Idempotent per (event, user)."""
    seen = await db.scalar(
        select(EventView.id).where(EventView.event_id == event_id, EventView.user_id == user_id)
    )
    if seen:
        return
    db.add(EventView(event_id=event_id, user_id=user_id, source=source))
    try:
        await db.flush()
    except IntegrityError:
        # two taps racing on the unique constraint; the row exists either way
        await db.rollback()


async def event_funnel(db: AsyncSession, event_id) -> dict:
    def _regs(*conditions):
        stmt = select(func.count()).select_from(Registration).where(Registration.event_id == event_id)
        for condition in conditions:
            stmt = stmt.where(condition)
        return stmt

    viewed = int(
        await db.scalar(select(func.count()).select_from(EventView).where(EventView.event_id == event_id))
        or 0
    )
    started = int(await db.scalar(_regs()) or 0)
    confirmed = int(await db.scalar(_regs(Registration.status == RegistrationStatus.CONFIRMED)) or 0)
    waitlisted = int(await db.scalar(_regs(Registration.status == RegistrationStatus.WAITLISTED)) or 0)
    pending = int(await db.scalar(_regs(Registration.status == RegistrationStatus.PENDING)) or 0)
    ineligible = int(await db.scalar(_regs(Registration.status == RegistrationStatus.INELIGIBLE)) or 0)
    from_link = int(await db.scalar(_regs(Registration.source == "deep_link")) or 0)
    delivered = int(
        await db.scalar(
            select(func.count())
            .select_from(Delivery)
            .where(
                Delivery.event_id == event_id,
                Delivery.kind == "room_credentials",
                Delivery.status == DeliveryStatus.SENT,
            )
        )
        or 0
    )
    return {
        "viewed": max(viewed, started),
        "started": started,
        "confirmed": confirmed,
        "waitlisted": waitlisted,
        "pending": pending,
        "ineligible": ineligible,
        "from_link": from_link,
        "delivered": delivered,
    }


def _bar(value: int, top: int, width: int = 10) -> str:
    if top <= 0:
        return "░" * width
    filled = max(0, min(width, round(value / top * width)))
    return "█" * filled + "░" * (width - filled)


def _drop(stage: int, previous: int | None) -> str:
    if previous is None or previous <= 0 or stage >= previous:
        return ""
    return f" (−{previous - stage})"


def format_funnel(stats: dict) -> str:
    """A compact funnel that stays readable inside a Telegram message."""
    top = max(stats.get("viewed", 0), stats.get("started", 0), 1)
    rows = [
        ("کارت را دیدند", stats.get("viewed", 0), None),
        ("وارد ثبت‌نام شدند", stats.get("started", 0), stats.get("viewed", 0)),
        ("شرایط را کامل کردند", stats.get("confirmed", 0), stats.get("started", 0)),
        ("مشخصات را گرفتند", stats.get("delivered", 0), stats.get("confirmed", 0)),
    ]
    lines = ["📊 <b>قیف این کاستوم</b>"]
    for label, value, previous in rows:
        lines.append(f"<code>{_bar(value, top)}</code> {value} — {label}{_drop(value, previous)}")
    tail = []
    if stats.get("from_link"):
        tail.append(f"از لینک اختصاصی: {stats['from_link']}")
    if stats.get("waitlisted"):
        tail.append(f"صف انتظار: {stats['waitlisted']}")
    if stats.get("pending"):
        tail.append(f"ناقص: {stats['pending']}")
    if stats.get("ineligible"):
        tail.append(f"رد شدند: {stats['ineligible']}")
    if tail:
        lines.append("")
        lines.append(" | ".join(tail))
    return "\n".join(lines)


def biggest_drop(stats: dict) -> str | None:
    """One actionable sentence about where the organizer is losing people."""
    steps = [
        (
            "viewed",
            "started",
            "بیشتر کسانی که کارت را دیدند وارد ثبت‌نام نشدند — جایزه یا ساعت را واضح‌تر بنویسید.",
        ),
        (
            "started",
            "confirmed",
            "بیشترشان ثبت‌نام را شروع کردند ولی کامل نکردند — احتمالاً تعداد کانال‌های اجباری زیاد است.",
        ),
        (
            "confirmed",
            "delivered",
            "واجد شرایط بودند ولی مشخصات نگرفتند — احتمالاً درست قبل از ارسال از کانال خارج شدند.",
        ),
    ]
    worst = None
    worst_lost = 0
    for before, after, message in steps:
        lost = max(0, int(stats.get(before, 0)) - int(stats.get(after, 0)))
        if lost > worst_lost:
            worst_lost = lost
            worst = message
    if worst_lost < 3:
        return None
    return worst
