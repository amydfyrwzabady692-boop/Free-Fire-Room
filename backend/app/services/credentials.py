from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.enums import DeliveryStatus, EventStatus, JobStatus, RegistrationStatus
from app.core.logging import get_logger
from app.core.security import decrypt_secret, generate_unguessable_token
from app.models.channel import Channel, GlobalRequiredChannel
from app.models.event import Event, EventRequiredChannel, RoomCredential
from app.models.jobs import Delivery, ScheduledJob
from app.models.registration import Registration
from app.models.user import User
from app.services.telegram_ops import get_membership

log = get_logger(__name__)

PERMANENT_ERRORS = {"Forbidden", "bot was blocked", "user is deactivated", "chat not found"}


def _is_permanent(msg: str) -> bool:
    low = (msg or "").lower()
    return any(p.lower() in low for p in PERMANENT_ERRORS)


async def send_credentials_for_job(db: Session, job: ScheduledJob, bot: Bot, redis=None) -> dict:
    """Idempotent credential delivery. Safe to retry after worker restart."""
    event = db.get(Event, job.entity_id)
    if not event or event.status in {EventStatus.CANCELLED, EventStatus.REJECTED, EventStatus.DRAFT}:
        job.status = JobStatus.CANCELLED
        db.flush()
        return {"skipped": True, "reason": "event_inactive"}

    creds = db.scalar(select(RoomCredential).where(RoomCredential.event_id == event.id))
    if not creds or creds.purged_at:
        job.status = JobStatus.FAILED
        job.last_error = "missing_credentials"
        db.flush()
        return {"ok": False, "reason": "missing_credentials"}

    room_id = decrypt_secret(creds.room_id_encrypted)
    room_password = decrypt_secret(creds.room_password_encrypted)

    regs = db.scalars(
        select(Registration)
        .where(Registration.event_id == event.id, Registration.status == RegistrationStatus.CONFIRMED)
        .options(selectinload(Registration.user))
    ).all()

    channel_ids = _collect_required_chat_ids(db, event)
    sent = failed = skipped = 0

    for reg in regs:
        user = db.get(User, reg.user_id)
        if not user:
            skipped += 1
            continue
        idem = f"creds:{event.id}:{user.id}:{creds.version}"
        existing = db.scalar(select(Delivery).where(Delivery.idempotency_key == idem))
        if existing and existing.status == DeliveryStatus.SENT:
            skipped += 1
            continue

        eligible, reason = _recheck_user(db, bot, user, event, channel_ids)
        if not eligible:
            _upsert_delivery(
                db,
                user=user,
                event=event,
                job=job,
                idem=idem,
                status=DeliveryStatus.SKIPPED,
                error=reason,
            )
            reg.status = RegistrationStatus.INELIGIBLE
            reg.ineligible_reason = reason
            skipped += 1
            continue

        text = _render_credentials_message(event, user, room_id, room_password, creds.version)
        kb = None
        if event.reveal_button_enabled:
            token = generate_unguessable_token(12)
            if redis is not None:
                redis.setex(f"reveal:{token}", event.reveal_ttl_seconds, f"{event.id}:{user.id}:{creds.version}")
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="نمایش اطلاعات اتاق", callback_data=f"reveal:{token}")]
                ]
            )
            # Still send the credentials in private message; reveal is extra UX.
        try:
            msg = await bot.send_message(user.telegram_id, text, reply_markup=kb)
            _upsert_delivery(
                db,
                user=user,
                event=event,
                job=job,
                idem=idem,
                status=DeliveryStatus.SENT,
                telegram_message_id=msg.message_id,
            )
            sent += 1
        except TelegramRetryAfter as exc:
            _upsert_delivery(
                db, user=user, event=event, job=job, idem=idem, status=DeliveryStatus.FAILED, error="retry_after"
            )
            failed += 1
            await asyncio.sleep(exc.retry_after + 0.3)
        except TelegramForbiddenError as exc:
            user.is_bot_blocked = True
            _upsert_delivery(
                db,
                user=user,
                event=event,
                job=job,
                idem=idem,
                status=DeliveryStatus.PERMANENT_FAIL,
                error=str(exc),
            )
            failed += 1
        except TelegramBadRequest as exc:
            status = DeliveryStatus.PERMANENT_FAIL if _is_permanent(str(exc)) else DeliveryStatus.FAILED
            _upsert_delivery(db, user=user, event=event, job=job, idem=idem, status=status, error=str(exc))
            failed += 1

    creds.sent_at = datetime.now(UTC)
    job.status = JobStatus.DONE if failed == 0 or sent > 0 else JobStatus.FAILED
    job.completed_at = datetime.now(UTC)
    job.last_error = None if failed == 0 else f"failed={failed}"
    if event.status in {EventStatus.PUBLISHED, EventStatus.FULL}:
        event.status = EventStatus.STARTED
    db.flush()
    log.info("credentials_job_done", event_id=str(event.id), sent=sent, failed=failed, skipped=skipped)
    return {"sent": sent, "failed": failed, "skipped": skipped}


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


def _recheck_user(db: Session, bot: Bot, user: User, event: Event, chat_ids: list[int]) -> tuple[bool, str | None]:
    from app.core.enums import BanScope
    from app.services.bans import is_banned_sync

    if is_banned_sync(db, user, BanScope.PARTICIPATE):
        return False, "banned"
    # membership checks are async; workers should call async path. This helper is used inside async function.
    return True, None


async def _recheck_user_async(bot: Bot, user: User, chat_ids: list[int]) -> tuple[bool, str | None]:
    for chat_id in chat_ids:
        result = await get_membership(bot, chat_id, user.telegram_id)
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

    def esc(value: str) -> str:
        return html.escape(str(value or ""), quote=False)

    name = user.first_name or user.username or str(user.telegram_id)
    header = event.custom_credentials_message or "مشخصات اتاق کاستوم شما:"
    personal = f"\nشرکت‌کننده: {esc(name)} | کد ثبت‌نام: {str(user.id)[:8]}" if event.personalize_delivery else ""
    ver = f"\nنسخه اطلاعات: {version}" if version > 1 else ""
    return (
        f"{esc(header)}\n\n"
        f"کاستوم: {esc(event.title)}\n"
        f"Room ID: <code>{esc(room_id)}</code>\n"
        f"Password: <code>{esc(password)}</code>"
        f"{personal}{ver}\n\n"
        "این پیام فقط برای شماست. اسکرین‌شات و بازنشر را کاملاً نمی‌توانیم مسدود کنیم؛"
        " لطفاً اطلاعات را در اختیار دیگران نگذارید."
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
        _upsert_delivery(db, user=user, event=event, job=job, idem=idem, status=DeliveryStatus.SKIPPED, error=reason)
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
        _upsert_delivery(
            db, user=user, event=event, job=job, idem=idem, status=DeliveryStatus.PERMANENT_FAIL, error=str(exc)
        )
        return "blocked"
    except Exception as exc:  # noqa: BLE001
        _upsert_delivery(db, user=user, event=event, job=job, idem=idem, status=DeliveryStatus.FAILED, error=str(exc))
        return "failed"
