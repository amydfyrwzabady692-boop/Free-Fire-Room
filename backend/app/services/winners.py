from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.helpers import esc
from app.core.enums import (
    DeliveryStatus,
    EventStatus,
    RegistrationStatus,
    WinnerClaimStatus,
    WinnerMessageDirection,
)
from app.core.errors import ConflictError, ValidationAppError
from app.models.event import Event
from app.models.jobs import Delivery
from app.models.organizer import Organizer
from app.models.registration import Registration
from app.models.user import User
from app.models.winner import WinnerClaim, WinnerMessage
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


# ------------------------------------------------------------------ payout contact

MAX_CONTACT_LEN = 128


def normalize_payout_contact(raw: str) -> str:
    """Accept @handle, a t.me link, or a bare username; store one @handle."""
    text = " ".join((raw or "").split())
    if not text:
        raise ValidationAppError("payout_contact", "آیدی را بفرستید. نمونه: <code>@my_id</code>")
    low = text.lower()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "telegram.me/"):
        if low.startswith(prefix):
            text = text[len(prefix) :]
            break
    text = text.lstrip("@").strip("/")
    if not text:
        raise ValidationAppError("payout_contact", "آیدی را کامل بفرستید. نمونه: <code>@my_id</code>")
    if len(text) > MAX_CONTACT_LEN:
        raise ValidationAppError("payout_contact", "این آیدی خیلی بلند است.")
    if " " in text:
        raise ValidationAppError(
            "payout_contact",
            "آیدی نباید فاصله داشته باشد. نمونه: <code>@my_id</code>",
        )
    return f"@{text}"


def player_dm_link(user: User | None) -> str | None:
    """A direct link to the player, so the organizer can also go outside the bot."""
    handle = (getattr(user, "username", None) or "").strip().lstrip("@")
    return f"https://t.me/{handle}" if handle else None


def contact_link(contact: str | None) -> str | None:
    handle = (contact or "").strip().lstrip("@")
    if not handle or " " in handle:
        return None
    return f"https://t.me/{handle}"


async def resolve_payout_contact(db: AsyncSession, event: Event) -> str | None:
    """What an approved winner is told to message, best source first."""
    direct = (getattr(event, "payout_contact", None) or "").strip()
    if direct:
        return direct
    org = await db.get(Organizer, event.organizer_id) if event.organizer_id else None
    if org and (org.payout_contact or "").strip():
        return org.payout_contact.strip()
    if org:
        user = await db.get(User, org.user_id)
        if user and user.username:
            return f"@{user.username.lstrip('@')}"
    return None


def format_payout_note(event: Event, contact: str | None) -> str:
    prize = esc((event.prize_summary or "").strip() or "—")
    head = (
        "🏆 <b>برنده بودن شما تأیید شد</b>\n"
        f"کاستوم: {esc(event.title)}\n"
        f"🎁 جایزه: {prize}\n"
        "━━━━━━━━━━━━━━\n"
    )
    if contact:
        return (
            head
            + f"برای دریافت جایزه به آیدی زیر پیام بدهید:\n<b>{esc(contact)}</b>\n\n"
            "می‌توانید از همین‌جا هم با دکمهٔ «پاسخ به برگزارکننده» پیام بدهید؛ "
            "پیام شما مستقیم برای برگزارکننده می‌رود."
        )
    return (
        head
        + "برگزارکننده هنوز آیدی دریافت جایزه را ثبت نکرده است.\n"
        "از دکمهٔ «پاسخ به برگزارکننده» همین‌جا پیام بدهید تا هماهنگ شود."
    )


# ------------------------------------------------------------------ review + relay


async def load_claim(db: AsyncSession, claim_id) -> WinnerClaim | None:
    return await db.scalar(
        select(WinnerClaim)
        .where(WinnerClaim.id == claim_id)
        .options(selectinload(WinnerClaim.event), selectinload(WinnerClaim.user))
    )


async def claim_parties(db: AsyncSession, claim: WinnerClaim) -> tuple[User | None, User | None]:
    """(winner, organizer) as User rows."""
    winner = await db.get(User, claim.user_id)
    organizer_user = None
    if claim.organizer_id:
        org = await db.get(Organizer, claim.organizer_id)
        if org:
            organizer_user = await db.get(User, org.user_id)
    return winner, organizer_user


async def resolve_claim(
    db: AsyncSession, claim: WinnerClaim, *, approved: bool, reviewer_id
) -> None:
    from datetime import UTC, datetime

    claim.status = WinnerClaimStatus.APPROVED if approved else WinnerClaimStatus.REJECTED
    claim.reviewed_by = reviewer_id
    claim.reviewed_at = datetime.now(UTC)
    await db.flush()


async def record_message(
    db: AsyncSession,
    *,
    claim: WinnerClaim,
    sender_id,
    body: str,
    delivered: bool,
    direction: str = WinnerMessageDirection.TO_WINNER,
) -> WinnerMessage:
    row = WinnerMessage(
        claim_id=claim.id,
        sender_id=sender_id,
        direction=direction,
        body=body[:4000],
        delivered=delivered,
    )
    db.add(row)
    await db.flush()
    return row


def format_relayed_to_winner(event: Event, body: str) -> str:
    return (
        "✉️ <b>پیام برگزارکننده</b>\n"
        f"کاستوم: {esc(event.title)}\n"
        "━━━━━━━━━━━━━━\n"
        f"{esc(body)}\n\n"
        "برای جواب دادن دکمهٔ پایین را بزنید."
    )


def format_relayed_to_organizer(event: Event, player: User, body: str) -> str:
    return (
        "✉️ <b>پیام برنده</b>\n"
        f"کاستوم: {esc(event.title)}\n"
        f"بازیکن: {format_person(player)}\n"
        "━━━━━━━━━━━━━━\n"
        f"{esc(body)}\n\n"
        "برای جواب دادن دکمهٔ پایین را بزنید."
    )


async def claims_for_organizer(
    db: AsyncSession, organizer_id, *, limit: int = 60
) -> list[WinnerClaim]:
    rows = (
        await db.scalars(
            select(WinnerClaim)
            .where(WinnerClaim.organizer_id == organizer_id)
            .options(selectinload(WinnerClaim.event), selectinload(WinnerClaim.user))
            .order_by(WinnerClaim.created_at.desc())
            .limit(limit)
        )
    ).all()
    return list(rows)


async def pending_claim_count(db: AsyncSession, organizer_id) -> int:
    from sqlalchemy import func

    return int(
        await db.scalar(
            select(func.count())
            .select_from(WinnerClaim)
            .where(
                WinnerClaim.organizer_id == organizer_id,
                WinnerClaim.status == WinnerClaimStatus.PENDING,
            )
        )
        or 0
    )
