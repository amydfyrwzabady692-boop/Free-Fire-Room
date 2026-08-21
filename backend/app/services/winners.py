from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.helpers import esc
from app.core.enums import DeliveryStatus, EventStatus, RegistrationStatus
from app.core.errors import ConflictError, ValidationAppError
from app.models.event import Event
from app.models.jobs import Delivery
from app.models.organizer import Organizer
from app.models.registration import Registration
from app.models.user import User
from app.models.winner import WinnerClaim
from app.services.reports import format_person

INELIGIBLE_MSG = (
    "شرایط این کاستوم را انجام نداده‌اید و ROOM ID / PASS را از ربات نگرفته‌اید؛ "
    "جایزه تعلق نمی‌گیرد."
)


def winner_eligible(*, confirmed: bool, received_creds: bool) -> bool:
    return bool(confirmed and received_creds)


async def user_received_credentials(db: AsyncSession, *, event_id, user_id) -> bool:
    row = await db.scalar(
        select(Delivery).where(
            Delivery.event_id == event_id,
            Delivery.user_id == user_id,
            Delivery.kind == "room_credentials",
            Delivery.status == DeliveryStatus.SENT,
        )
    )
    return row is not None


async def check_winner_eligibility(db: AsyncSession, user: User, event: Event) -> str | None:
    if event.deleted_at or event.status in {EventStatus.DRAFT, EventStatus.REJECTED, EventStatus.CANCELLED}:
        return "این کاستوم برای اعلام برنده در دسترس نیست."
    reg = await db.scalar(
        select(Registration).where(Registration.event_id == event.id, Registration.user_id == user.id)
    )
    confirmed = bool(reg and reg.status == RegistrationStatus.CONFIRMED)
    received = await user_received_credentials(db, event_id=event.id, user_id=user.id)
    if not winner_eligible(confirmed=confirmed, received_creds=received):
        return INELIGIBLE_MSG
    return None


async def create_winner_claim(
    db: AsyncSession,
    *,
    user: User,
    event: Event,
    screenshot_file_id: str,
) -> WinnerClaim:
    reason = await check_winner_eligibility(db, user, event)
    if reason:
        raise ValidationAppError("winner_ineligible", reason)
    existing = await db.scalar(
        select(WinnerClaim).where(WinnerClaim.event_id == event.id, WinnerClaim.user_id == user.id)
    )
    if existing:
        raise ConflictError("winner_already_claimed", "قبلاً برای این کاستوم اسکرین برنده فرستاده‌اید.")
    claim = WinnerClaim(
        event_id=event.id,
        user_id=user.id,
        organizer_id=event.organizer_id,
        screenshot_file_id=screenshot_file_id,
    )
    db.add(claim)
    await db.flush()
    return claim


def format_winner_claim_caption(event: Event, player: User) -> str:
    prize = esc((event.prize_summary or "").strip() or "—")
    return (
        "🏆 <b>ادعای برنده</b>\n"
        f"کاستوم: {esc(event.title)}\n"
        f"جایزه: {prize}\n"
        f"بازیکن: {format_person(player)}\n\n"
        "این بازیکن شرایط جوین را انجام داده و ROOM ID / PASS را از ربات گرفته است."
    )


async def list_recent_winner_events(db: AsyncSession, *, hours: int = 48, limit: int = 15) -> list[Event]:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    rows = (
        await db.scalars(
            select(Event)
            .where(
                Event.deleted_at.is_(None),
                Event.starts_at <= now,
                Event.starts_at >= now - timedelta(hours=hours),
                Event.status.in_(
                    [EventStatus.PUBLISHED, EventStatus.FULL, EventStatus.STARTED, EventStatus.FINISHED]
                ),
            )
            .options(selectinload(Event.organizer))
            .order_by(Event.starts_at.desc())
            .limit(limit)
        )
    ).all()
    return list(rows)


async def list_winner_claims(db: AsyncSession, *, limit: int = 20) -> list[WinnerClaim]:
    rows = (
        await db.scalars(
            select(WinnerClaim)
            .options(selectinload(WinnerClaim.event), selectinload(WinnerClaim.user))
            .order_by(WinnerClaim.created_at.desc())
            .limit(limit)
        )
    ).all()
    return list(rows)


async def organizer_telegram_id(db: AsyncSession, organizer_id) -> int | None:
    org = await db.get(Organizer, organizer_id)
    if not org:
        return None
    user = await db.get(User, org.user_id)
    return user.telegram_id if user else None
