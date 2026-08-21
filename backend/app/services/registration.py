from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.enums import EventStatus, RegistrationStatus, RequirementStatus
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from app.core.logging import get_logger
from app.models.event import Event
from app.models.registration import Registration, WaitlistEntry
from app.models.user import User
from app.services.audit import write_audit
from app.services.bans import assert_not_banned
from app.core.enums import BanScope
from app.services.reports import join_window_open
from app.services.requirements import evaluate_requirements

log = get_logger(__name__)

_WEAK_SOURCES = {None, "", "recheck", "rules"}


def merge_registration_source(existing: str | None, incoming: str | None) -> str | None:
    if incoming == "deep_link" or existing == "deep_link":
        return "deep_link"
    if incoming in _WEAK_SOURCES:
        return existing or incoming
    return existing or incoming


@dataclass
class RegisterResult:
    registration: Registration
    promoted_from_waitlist: bool = False
    waitlisted: bool = False
    checklist: list | None = None


async def get_event_or_404(db: AsyncSession, event_id: UUID) -> Event:
    event = await db.get(Event, event_id)
    if not event or event.deleted_at:
        raise NotFoundError("event_not_found", "کاستوم یافت نشد.")
    return event


async def register_user(
    db: AsyncSession,
    *,
    user: User,
    event: Event,
    bot,
    source: str | None = None,
    accept_rules: bool = False,
    actor_ip: str | None = None,
) -> RegisterResult:
    await assert_not_banned(db, user, BanScope.PARTICIPATE)

    if event.deleted_at is not None or not event.deep_link_active:
        raise NotFoundError("event_not_found", "این کاستوم در دسترس نیست یا لغو شده است.")

    if event.status not in {EventStatus.PUBLISHED, EventStatus.FULL, EventStatus.STARTED}:
        raise ValidationAppError("event_not_open", "این کاستوم در حال حاضر برای ثبت‌نام باز نیست.")

    now = datetime.now(UTC)
    if not join_window_open(event, now):
        raise ValidationAppError("registration_closed", "مهلت جوین و پر شدن این کاستوم تمام شده است.")

    existing = await db.scalar(
        select(Registration).where(Registration.event_id == event.id, Registration.user_id == user.id)
    )
    if existing and existing.status in {RegistrationStatus.CONFIRMED, RegistrationStatus.WAITLISTED}:
        merged = merge_registration_source(existing.source, source)
        if merged != existing.source:
            existing.source = merged
            await db.flush()
        raise ConflictError("already_registered", "شما قبلاً در این کاستوم ثبت‌نام کرده‌اید.")

    holder = existing
    if holder is None:
        holder = Registration(
            event_id=event.id,
            user_id=user.id,
            status=RegistrationStatus.PENDING,
            source=source,
            rules_accepted_at=now if accept_rules else None,
        )
        db.add(holder)
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError as exc:
            raise ConflictError("already_registered", "شما قبلاً در این کاستوم ثبت‌نام کرده‌اید.") from exc
    else:
        holder.source = merge_registration_source(holder.source, source)
        if accept_rules:
            holder.rules_accepted_at = now

    checklist = await evaluate_requirements(db, user=user, event=event, bot=bot, registration=holder)
    blocking = [i for i in checklist.items if i.status not in {RequirementStatus.DONE, RequirementStatus.PENDING_REVIEW}]
    capacity_pending = any(i.requirement_type == "capacity" and i.status != RequirementStatus.DONE for i in checklist.items)

    if any(i.status == RequirementStatus.REJECTED for i in checklist.items):
        holder.status = RegistrationStatus.INELIGIBLE
        holder.ineligible_reason = "شرایط رد شده است."
        await db.flush()
        return RegisterResult(holder, checklist=checklist.items)

    hard_missing = [
        i
        for i in checklist.items
        if i.status in {RequirementStatus.NOT_DONE, RequirementStatus.EXPIRED} and i.requirement_type != "capacity"
    ]
    if hard_missing:
        holder.status = RegistrationStatus.PENDING
        await db.flush()
        return RegisterResult(holder, checklist=checklist.items)

    # Capacity lock
    locked = await db.scalar(select(Event).where(Event.id == event.id).with_for_update())
    if locked is None:
        raise NotFoundError("event_not_found", "کاستوم یافت نشد.")

    if locked.confirmed_count < locked.capacity:
        holder.status = RegistrationStatus.CONFIRMED
        holder.confirmed_at = now
        holder.conditions_met_at = now
        locked.confirmed_count += 1
        if locked.confirmed_count >= locked.capacity and locked.status == EventStatus.PUBLISHED:
            locked.status = EventStatus.FULL
        await write_audit(
            db,
            action="registration_confirmed",
            entity_type="registration",
            entity_id=holder.id,
            actor_id=user.id,
            actor_telegram_id=user.telegram_id,
            ip_address=actor_ip,
            extra={"event_id": str(event.id), "telegram_id": user.telegram_id},
        )
        await db.flush()
        return RegisterResult(holder, checklist=checklist.items)

    if locked.waitlist_enabled:
        last_pos = await db.scalar(
            select(WaitlistEntry.position)
            .where(WaitlistEntry.event_id == locked.id, WaitlistEntry.is_active.is_(True))
            .order_by(WaitlistEntry.position.desc())
        )
        pos = int(last_pos or 0) + 1
        holder.status = RegistrationStatus.WAITLISTED
        holder.waitlist_position = pos
        db.add(
            WaitlistEntry(
                event_id=locked.id,
                user_id=user.id,
                registration_id=holder.id,
                position=pos,
                is_active=True,
            )
        )
        if locked.status == EventStatus.PUBLISHED:
            locked.status = EventStatus.FULL
        await db.flush()
        return RegisterResult(holder, waitlisted=True, checklist=checklist.items)

    raise ConflictError("event_full", "ظرفیت این کاستوم تکمیل شده است.")


async def mark_ineligible(db: AsyncSession, registration: Registration, reason: str) -> None:
    if registration.status == RegistrationStatus.CONFIRMED:
        event = await db.get(Event, registration.event_id)
        if event and event.confirmed_count > 0:
            event.confirmed_count -= 1
            if event.status == EventStatus.FULL and event.confirmed_count < event.capacity:
                event.status = EventStatus.PUBLISHED
        registration.status = RegistrationStatus.INELIGIBLE
        registration.ineligible_reason = reason
        await db.flush()
        await promote_waitlist(db, registration.event_id)
    elif registration.status == RegistrationStatus.WAITLISTED:
        registration.status = RegistrationStatus.INELIGIBLE
        registration.ineligible_reason = reason
        wl = await db.scalar(
            select(WaitlistEntry).where(
                WaitlistEntry.registration_id == registration.id, WaitlistEntry.is_active.is_(True)
            )
        )
        if wl:
            wl.is_active = False
        await db.flush()


async def promote_waitlist(db: AsyncSession, event_id: UUID) -> Registration | None:
    event = await db.scalar(select(Event).where(Event.id == event_id).with_for_update())
    if not event or event.confirmed_count >= event.capacity:
        return None
    entry = await db.scalar(
        select(WaitlistEntry)
        .where(WaitlistEntry.event_id == event_id, WaitlistEntry.is_active.is_(True))
        .order_by(WaitlistEntry.position.asc())
        .limit(1)
    )
    if not entry:
        return None
    reg = await db.get(Registration, entry.registration_id)
    if not reg or reg.status != RegistrationStatus.WAITLISTED:
        entry.is_active = False
        await db.flush()
        return await promote_waitlist(db, event_id)
    now = datetime.now(UTC)
    reg.status = RegistrationStatus.CONFIRMED
    reg.confirmed_at = now
    event.confirmed_count += 1
    entry.is_active = False
    entry.promoted_at = now
    if event.confirmed_count >= event.capacity:
        if event.status == EventStatus.PUBLISHED:
            event.status = EventStatus.FULL
    elif event.status == EventStatus.FULL:
        event.status = EventStatus.PUBLISHED
    await db.flush()
    return reg


def try_confirm_with_lock_sync(db: Session, user_id, event_id) -> str:
    """Used in concurrency tests / workers. Returns confirmed|waitlisted|full|exists."""
    event = db.scalar(select(Event).where(Event.id == event_id).with_for_update())
    if event is None:
        return "missing"
    existing = db.scalar(
        select(Registration).where(Registration.event_id == event_id, Registration.user_id == user_id)
    )
    if existing and existing.status in {RegistrationStatus.CONFIRMED, RegistrationStatus.WAITLISTED}:
        return "exists"
    now = datetime.now(UTC)
    if existing is None:
        existing = Registration(event_id=event_id, user_id=user_id, status=RegistrationStatus.PENDING)
        db.add(existing)
        db.flush()
    if event.confirmed_count < event.capacity:
        existing.status = RegistrationStatus.CONFIRMED
        existing.confirmed_at = now
        event.confirmed_count += 1
        if event.confirmed_count >= event.capacity:
            event.status = EventStatus.FULL
        db.flush()
        return "confirmed"
    if event.waitlist_enabled:
        existing.status = RegistrationStatus.WAITLISTED
        db.flush()
        return "waitlisted"
    return "full"
