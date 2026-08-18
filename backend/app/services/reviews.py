from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.enums import DeliveryStatus, PrizePaidVote, RegistrationStatus, TrustEventType
from app.models.event import Event
from app.models.jobs import Delivery
from app.models.organizer import Organizer
from app.models.registration import Registration
from app.models.review import EventReview
from app.models.user import User
from app.services.organizers import apply_trust

PRIZE_LABELS = {
    PrizePaidVote.YES: "جایزه داد",
    PrizePaidVote.NO: "جایزه نداد",
    PrizePaidVote.UNKNOWN: "نمی‌دانم",
}


def stars(rating: float) -> str:
    filled = max(0, min(5, int(round(rating))))
    return "★" * filled + "☆" * (5 - filled)


def prize_label(value: str) -> str:
    try:
        return PRIZE_LABELS.get(PrizePaidVote(value), value)
    except ValueError:
        return value


async def review_summary_for_event(db: AsyncSession, event_id) -> dict:
    count = await db.scalar(
        select(func.count()).select_from(EventReview).where(EventReview.event_id == event_id)
    )
    avg = await db.scalar(select(func.avg(EventReview.rating)).where(EventReview.event_id == event_id))
    yes = await db.scalar(
        select(func.count()).select_from(EventReview).where(
            EventReview.event_id == event_id, EventReview.prize_paid == PrizePaidVote.YES
        )
    )
    no = await db.scalar(
        select(func.count()).select_from(EventReview).where(
            EventReview.event_id == event_id, EventReview.prize_paid == PrizePaidVote.NO
        )
    )
    return {"count": int(count or 0), "avg": float(avg or 0), "prize_yes": int(yes or 0), "prize_no": int(no or 0)}


async def review_summary_for_organizer(db: AsyncSession, organizer_id) -> dict:
    count = await db.scalar(
        select(func.count()).select_from(EventReview).where(EventReview.organizer_id == organizer_id)
    )
    avg = await db.scalar(
        select(func.avg(EventReview.rating)).where(EventReview.organizer_id == organizer_id)
    )
    yes = await db.scalar(
        select(func.count()).select_from(EventReview).where(
            EventReview.organizer_id == organizer_id, EventReview.prize_paid == PrizePaidVote.YES
        )
    )
    no = await db.scalar(
        select(func.count()).select_from(EventReview).where(
            EventReview.organizer_id == organizer_id, EventReview.prize_paid == PrizePaidVote.NO
        )
    )
    return {
        "count": int(count or 0),
        "avg": float(avg or 0),
        "prize_yes": int(yes or 0),
        "prize_no": int(no or 0),
    }


def format_rating_line(summary: dict, *, prefix: str = "امتیاز") -> str:
    if not summary["count"]:
        return f"{prefix}: هنوز نظری ثبت نشده"
    avg = summary["avg"]
    return f"{prefix}: {stars(avg)} {avg:.1f} از ۵ ({summary['count']} نظر)"


async def list_event_reviews(db: AsyncSession, event_id, limit: int = 8) -> list[EventReview]:
    rows = (
        await db.scalars(
            select(EventReview)
            .where(EventReview.event_id == event_id)
            .options(selectinload(EventReview.reviewer))
            .order_by(EventReview.created_at.desc())
            .limit(limit)
        )
    ).all()
    return list(rows)


async def existing_review(db: AsyncSession, user_id, event_id) -> EventReview | None:
    return await db.scalar(
        select(EventReview).where(EventReview.reviewer_id == user_id, EventReview.event_id == event_id)
    )


def review_window_open(event: Event, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    hours = get_settings().past_events_hours
    return event.starts_at <= now <= event.starts_at + timedelta(hours=hours)


async def can_review(db: AsyncSession, user: User, event: Event) -> tuple[bool, str | None]:
    if event.deleted_at is not None or not event.deep_link_active:
        return False, "این کاستوم در دسترس نیست."
    org = event.organizer
    if org and org.user_id == user.id:
        return False, "نمی‌توانید برای کاستوم خودتان نظر بگذارید."
    if not review_window_open(event):
        return False, "فقط تا ۴۸ ساعت بعد از ساعت کاستوم می‌توان نظر ثبت کرد."
    if await existing_review(db, user.id, event.id):
        return False, "قبلاً برای این کاستوم نظر ثبت کرده‌اید."
    reg = await db.scalar(
        select(Registration).where(Registration.event_id == event.id, Registration.user_id == user.id)
    )
    if not reg or reg.status != RegistrationStatus.CONFIRMED:
        return False, "فقط کسانی که در این کاستوم ثبت‌نام قطعی شده‌اند می‌توانند نظر بدهند."
    return True, None


def _trust_delta(rating: int, prize_paid: str) -> float:
    delta = {5: 2.0, 4: 1.0, 3: 0.0, 2: -2.0, 1: -4.0}.get(rating, 0.0)
    if prize_paid == PrizePaidVote.YES:
        delta += 1.0
    elif prize_paid == PrizePaidVote.NO:
        delta -= 3.0
    return delta


async def create_review(
    db: AsyncSession,
    *,
    user: User,
    event: Event,
    rating: int,
    prize_paid: str,
    comment: str | None,
) -> tuple[EventReview | None, str | None]:
    ok, err = await can_review(db, user, event)
    if not ok:
        return None, err
    if rating not in {1, 2, 3, 4, 5}:
        return None, "امتیاز باید بین ۱ تا ۵ باشد."
    try:
        prize = PrizePaidVote(prize_paid)
    except ValueError:
        prize = PrizePaidVote.UNKNOWN
    text = (comment or "").strip()[:400] or None
    row = EventReview(
        reviewer_id=user.id,
        event_id=event.id,
        organizer_id=event.organizer_id,
        rating=rating,
        prize_paid=prize.value,
        comment=text,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return None, "قبلاً برای این کاستوم نظر ثبت کرده‌اید."
    org = event.organizer or await db.get(Organizer, event.organizer_id)
    if org:
        delta = _trust_delta(rating, prize.value)
        if delta:
            apply_trust(
                db,
                org,
                TrustEventType.PLAYER_REVIEW,
                delta,
                f"نظر بازیکن: {rating} ستاره",
                actor_id=user.id,
                related=event.id,
            )
    await db.flush()
    return row, None


async def event_audience_stats(db: AsyncSession, event_id) -> dict:
    def _count(status: str | None = None):
        stmt = select(func.count()).select_from(Registration).where(Registration.event_id == event_id)
        if status:
            stmt = stmt.where(Registration.status == status)
        return stmt

    total = int(await db.scalar(_count()) or 0)
    confirmed = int(await db.scalar(_count(RegistrationStatus.CONFIRMED)) or 0)
    pending = int(await db.scalar(_count(RegistrationStatus.PENDING)) or 0)
    waitlisted = int(await db.scalar(_count(RegistrationStatus.WAITLISTED)) or 0)
    ineligible = int(await db.scalar(_count(RegistrationStatus.INELIGIBLE)) or 0)
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
        "total": total,
        "confirmed": confirmed,
        "pending": pending,
        "waitlisted": waitlisted,
        "ineligible": ineligible,
        "delivered": delivered,
    }


def format_audience_stats(stats: dict) -> str:
    return (
        f"عضو آمده / ثبت‌نام کل: {stats['total']}\n"
        f"شرایط را کامل کردند: {stats['confirmed']}\n"
        f"هنوز جوین نکرده‌اند: {stats['pending']}\n"
        f"لیست انتظار: {stats['waitlisted']}\n"
        f"واجد شرایط نشدند: {stats['ineligible']}\n"
        f"رمز برایشان ارسال شد: {stats['delivered']}"
    )


def format_review_item(row: EventReview) -> str:
    from app.bot.helpers import esc

    name = "بازیکن"
    if row.reviewer:
        name = row.reviewer.first_name or row.reviewer.username or "بازیکن"
    comment = f"\n«{esc(row.comment)}»" if row.comment else ""
    return f"{stars(row.rating)} {esc(name)}{comment}"
