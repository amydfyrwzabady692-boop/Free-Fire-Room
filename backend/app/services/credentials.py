from __future__ import annotations

from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import DeliveryStatus, RegistrationStatus
from app.core.logging import get_logger
from app.core.security import decrypt_secret
from app.models.channel import Channel, GlobalRequiredChannel
from app.models.event import Event, EventRequiredChannel, RoomCredential
from app.models.jobs import Delivery, ScheduledJob
from app.models.registration import Registration
from app.models.user import User
from app.services.telegram_ops import CHECK_UNAVAILABLE, get_membership

log = get_logger(__name__)


def _collect_required_chat_ids(db: Session, event: Event) -> list[int]:
    ids: list[int] = []
    globals_ = db.scalars(
        select(GlobalRequiredChannel).where(
            GlobalRequiredChannel.is_active.is_(True), GlobalRequiredChannel.applies_to_events.is_(True)
        )
    ).all()
    for g in globals_:
        ch = db.get(Channel, g.channel_id)
        if ch:
            ids.append(ch.telegram_chat_id)
    locals_ = db.scalars(
        select(EventRequiredChannel).where(
            EventRequiredChannel.event_id == event.id, EventRequiredChannel.is_active.is_(True)
        )
    ).all()
    for e in locals_:
        ch = db.get(Channel, e.channel_id)
        if ch:
            ids.append(ch.telegram_chat_id)
    return ids


async def _recheck_user_async(bot: Bot, user: User, chat_ids: list[int]) -> tuple[bool, str | None]:
    """Returns (eligible, reason). A reason of ``bot_not_admin`` or
    ``check_unavailable`` means the check could not be completed - the caller
    must retry rather than treat the player as ineligible."""
    for chat_id in chat_ids:
        result = await get_membership(bot, chat_id, user.telegram_id)
        if result.unknown:
            return False, result.error
        if not result.ok:
            return False, f"left_channel:{chat_id}"
    return True, None


def _upsert_delivery(db: Session, *, user, event, job, idem, status, error=None, telegram_message_id=None):
    row = db.scalar(select(Delivery).where(Delivery.idempotency_key == idem))
    if row is None:
        row = Delivery(
            user_id=user.id,
            event_id=event.id,
            job_id=job.id,
            kind="room_credentials",
            status=status,
            idempotency_key=idem,
            error_message=error,
            telegram_message_id=telegram_message_id,
            attempts=1,
            sent_at=datetime.now(UTC) if status == DeliveryStatus.SENT else None,
        )
        db.add(row)
    else:
        if row.status == DeliveryStatus.SENT:
            return
        row.status = status
        row.attempts += 1
        row.error_message = error
        row.telegram_message_id = telegram_message_id or row.telegram_message_id
        if status == DeliveryStatus.SENT:
            row.sent_at = datetime.now(UTC)
    db.flush()


def _render_credentials_message(event: Event, user: User, room_id: str, password: str, version: int) -> str:
    import html

    from app.locales.style import GAME, GIFT, SEP, SHIELD, USER, room_pair

    def esc(value: str) -> str:
        return html.escape(str(value or ""), quote=False)

    name = user.first_name or user.username or str(user.telegram_id)
    prize = esc((event.prize_summary or "").strip() or "—")
    header = event.custom_credentials_message or "کاستوم آماده است"
    personal = f"\n{USER} {esc(name)} | کد ثبت‌نام: {str(user.id)[:8]}" if event.personalize_delivery else ""
    ver = f"\nنسخه اطلاعات: {version}" if version > 1 else ""
    return (
        f"{GAME} <b>{esc(header)}</b>\n"
        f"{SEP}\n"
        f"{esc(event.title)}\n"
        f"{GIFT} <b>جایزه</b>\n{prize}\n"
        f"{SEP}\n"
        f"{room_pair(esc(room_id), esc(password))}"
        f"{personal}{ver}\n"
        f"{SEP}\n"
        f"{SHIELD} این پیام فقط برای شماست. ROOM ID و PASS را برای دیگران نفرستید."
    )


# Patch send loop to use async membership recheck
async def deliver_one(bot: Bot, db: Session, event: Event, user: User, creds: RoomCredential, job: ScheduledJob, redis=None) -> str:
    idem = f"creds:{event.id}:{user.id}:{creds.version}"
    existing = db.scalar(select(Delivery).where(Delivery.idempotency_key == idem))
    if existing and existing.status == DeliveryStatus.SENT:
        return "already"
    chat_ids = _collect_required_chat_ids(db, event)
    ok, reason = await _recheck_user_async(bot, user, chat_ids)
    from app.core.enums import BanScope
    from app.services.bans import is_banned_sync

    if is_banned_sync(db, user, BanScope.PARTICIPATE):
        ok, reason = False, "banned"
    if not ok:
        # we could not verify this player: leave their registration alone and
        # let the job retry, instead of demoting them to "ineligible"
        if reason == "bot_not_admin":
            return "check_failed"
        if reason == CHECK_UNAVAILABLE:
            return "check_unavailable"
        _upsert_delivery(db, user=user, event=event, job=job, idem=idem, status=DeliveryStatus.SKIPPED, error=reason)
        reg = db.scalar(
            select(Registration).where(Registration.event_id == event.id, Registration.user_id == user.id)
        )
        if reg and reg.status == RegistrationStatus.CONFIRMED:
            if event.confirmed_count > 0:
                event.confirmed_count -= 1
            reg.status = RegistrationStatus.INELIGIBLE
            reg.ineligible_reason = reason
        return "skipped"
    room_id = decrypt_secret(creds.room_id_encrypted)
    password = decrypt_secret(creds.room_password_encrypted)
    text = _render_credentials_message(event, user, room_id, password, creds.version)
    try:
        msg = await bot.send_message(user.telegram_id, text)
        _upsert_delivery(
            db,
            user=user,
            event=event,
            job=job,
            idem=idem,
            status=DeliveryStatus.SENT,
            telegram_message_id=msg.message_id,
        )
        return "sent"
    except TelegramForbiddenError as exc:
        user.is_bot_blocked = True
        _upsert_delivery(
            db, user=user, event=event, job=job, idem=idem, status=DeliveryStatus.PERMANENT_FAIL, error=str(exc)
        )
        return "blocked"
    except Exception as exc:  # noqa: BLE001
        _upsert_delivery(db, user=user, event=event, job=job, idem=idem, status=DeliveryStatus.FAILED, error=str(exc))
        return "failed"
