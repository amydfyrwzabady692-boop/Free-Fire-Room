from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.enums import EventStatus, GameMode, RequirementType
from app.core.errors import ForbiddenError, ValidationAppError
from app.core.security import encrypt_secret, generate_unguessable_token
from app.core.time import as_utc
from app.models.event import Event, EventPrize, EventRequiredChannel, EventRequirement, RoomCredential
from app.models.organizer import Organizer
from app.services.audit import write_audit
from app.services.scheduler import cancel_event_jobs, schedule_event_jobs
from app.services.settings import get_setting

#: The custom may start the moment it is created. The organizer decides when
#: joining closes by tapping "custom started"; the clock no longer does it.
MIN_START_LEAD_MINUTES = 0

#: capacity 0 means "as many players as show up"
UNLIMITED_CAPACITY = 0


def capacity_is_unlimited(event: Event) -> bool:
    return int(event.capacity or 0) <= 0


def _validate_times(starts_at, registration_ends_at, credentials_send_at) -> None:
    starts_at = as_utc(starts_at)
    registration_ends_at = as_utc(registration_ends_at)
    credentials_send_at = as_utc(credentials_send_at)
    now = datetime.now(UTC)
    if starts_at < now - timedelta(minutes=1):
        raise ValidationAppError(
            "starts_in_past",
            "این ساعت گذشته است. ساعتی از الان به بعد بفرستید.",
        )
    if credentials_send_at > starts_at:
        raise ValidationAppError("creds_after_start", "ارسال ROOM ID / PASS نمی‌تواند بعد از شروع بازی باشد.")
    if registration_ends_at < credentials_send_at:
        raise ValidationAppError("reg_before_creds", "مهلت جوین باید تا بعد از ارسال ROOM ID / PASS باز بماند.")


async def create_event(db: AsyncSession, organizer: Organizer, data: dict, actor_id) -> Event:
    settings = get_settings()
    max_events = organizer.max_events or await get_setting(db, "max_events_per_organizer", settings.max_events_per_organizer)
    count_stmt = select(Event).where(
        Event.organizer_id == organizer.id,
        Event.deleted_at.is_(None),
        Event.status.in_(
            [EventStatus.DRAFT, EventStatus.PENDING_APPROVAL, EventStatus.PUBLISHED, EventStatus.FULL, EventStatus.STARTED]
        ),
    )
    from sqlalchemy import func as _func

    existing = int(await db.scalar(select(_func.count()).select_from(count_stmt.subquery())) or 0)
    if existing >= int(max_events):
        raise ForbiddenError("event_quota", "به سقف تعداد کاستوم فعال رسیده‌اید.")

    max_refs = int(await get_setting(db, "max_required_referrals", settings.max_required_referrals))
    required_referrals = int(data.get("required_referrals") or 0)
    if required_referrals > max_refs:
        raise ValidationAppError("referrals_cap", f"سقف دعوت مجاز {max_refs} است.")

    starts_at = as_utc(data["starts_at"])
    registration_ends_at = as_utc(data["registration_ends_at"])
    credentials_send_at = as_utc(data["credentials_send_at"])
    _validate_times(starts_at, registration_ends_at, credentials_send_at)

    # 0 (the default) means unlimited: everyone who completes the conditions
    # gets in and gets the ROOM ID / PASS.
    capacity = max(0, int(data.get("capacity") or 0))

    event = Event(
        public_token=generate_unguessable_token(18),
        organizer_id=organizer.id,
        channel_id=data.get("channel_id"),
        title=data["title"].strip(),
        description=data.get("description"),
        banner_file_id=data.get("banner_file_id"),
        starts_at=starts_at,
        registration_ends_at=registration_ends_at,
        credentials_send_at=credentials_send_at,
        timezone=data.get("timezone") or "Asia/Tehran",
        region=data.get("region") or "ME",
        game_mode=data.get("game_mode") or GameMode.SQUAD,
        capacity=capacity,
        waitlist_enabled=bool(data.get("waitlist_enabled", True)),
        visibility=data.get("visibility") or "public",
        status=EventStatus.DRAFT,
        require_rules_accept=bool(data.get("require_rules_accept", True)),
        require_ff_player_id=bool(data.get("require_ff_player_id", False)),
        require_profile_complete=bool(data.get("require_profile_complete", False)),
        required_referrals=required_referrals,
        rules_text=data.get("rules_text"),
        winner_method=data.get("winner_method"),
        custom_credentials_message=data.get("custom_credentials_message"),
        reveal_button_enabled=bool(data.get("reveal_button_enabled", True)),
        personalize_delivery=bool(data.get("personalize_delivery", True)),
        reminder_offsets_minutes=data.get("reminder_offsets_minutes") or [60, 10],
        prize_summary=data.get("prize_summary"),
        payout_contact=(data.get("payout_contact") or None),
        social_url=(data.get("social_url") or None),
        social_platform=(data.get("social_platform") or None),
        social_note=(data.get("social_note") or None),
        deep_link_active=True,
    )
    db.add(event)
    await db.flush()

    for i, prize in enumerate(data.get("prizes") or []):
        db.add(
            EventPrize(
                event_id=event.id,
                place=int(prize.get("place") or i + 1),
                title=prize["title"],
                description=prize.get("description"),
                estimated_value=prize.get("estimated_value"),
                sort_order=i,
            )
        )

    _seed_requirements(db, event, data)

    room_id = data.get("room_id")
    room_password = data.get("room_password")
    if room_id and room_password:
        db.add(
            RoomCredential(
                event_id=event.id,
                room_id_encrypted=encrypt_secret(room_id),
                room_password_encrypted=encrypt_secret(room_password),
                last_changed_by=actor_id,
            )
        )

    await write_audit(
        db,
        action="event_created",
        entity_type="event",
        entity_id=event.id,
        actor_id=actor_id,
        extra={"title": event.title},
    )
    await db.flush()
    return event


def _requirement_config(event: Event, rtype: str) -> dict | None:
    if rtype == RequirementType.REFERRALS:
        return {"required_referrals": event.required_referrals}
    if rtype == RequirementType.SOCIAL_FOLLOW:
        return {"url": event.social_url, "platform": event.social_platform, "note": event.social_note}
    return None


def _seed_requirements(db: AsyncSession, event: Event, data: dict) -> None:
    items = [
        (RequirementType.NOT_BANNED, "عدم محدودیت حساب", 0),
        (RequirementType.CAPACITY, "ظرفیت خالی", 1),
    ]
    if event.require_rules_accept:
        items.append((RequirementType.RULES_ACCEPT, "پذیرش قوانین", 2))
    if event.require_profile_complete:
        items.append((RequirementType.PROFILE_COMPLETE, "تکمیل پروفایل", 3))
    if event.require_ff_player_id:
        items.append((RequirementType.FF_PLAYER_ID, "شناسه Free Fire", 4))
    if event.required_referrals:
        items.append((RequirementType.REFERRALS, f"دعوت {event.required_referrals} نفر", 5))
    items.append((RequirementType.GLOBAL_CHANNEL_MEMBERSHIP, "عضویت کانال‌های اجباری ربات", 6))
    if (event.social_url or "").strip():
        items.append((RequirementType.SOCIAL_FOLLOW, "فالو پیج برگزارکننده + اسکرین‌شات", 7))
    for rtype, label, order in items:
        db.add(
            EventRequirement(
                event_id=event.id,
                requirement_type=rtype,
                label=label,
                sort_order=order,
                is_active=True,
                config=_requirement_config(event, rtype),
            )
        )
    for cid in data.get("required_channel_ids") or []:
        db.add(EventRequiredChannel(event_id=event.id, channel_id=cid, is_active=True))
        db.add(
            EventRequirement(
                event_id=event.id,
                requirement_type=RequirementType.CHANNEL_MEMBERSHIP,
                ref_id=str(cid),
                label="عضویت کانال برگزارکننده",
                is_active=True,
            )
        )


async def submit_for_publish(db: AsyncSession, event: Event, actor_id) -> Event:
    if not event.channel_id:
        raise ValidationAppError("channel_required", "کانال برگزارکننده را مشخص کنید.")
    approval = await get_setting(db, "event_approval_required", False)
    if approval:
        event.status = EventStatus.PENDING_APPROVAL
    else:
        event.status = EventStatus.PUBLISHED
        event.published_at = datetime.now(UTC)
        await schedule_event_jobs(db, event)
    await write_audit(db, action="event_submitted", entity_type="event", entity_id=event.id, actor_id=actor_id)
    await db.flush()
    return event


async def approve_event(db: AsyncSession, event: Event, actor_id) -> Event:
    if event.status not in {EventStatus.PENDING_APPROVAL, EventStatus.REJECTED, EventStatus.DRAFT}:
        raise ValidationAppError("bad_status", "این کاستوم قابل تأیید نیست.")
    event.status = EventStatus.PUBLISHED
    event.published_at = datetime.now(UTC)
    event.reviewed_by = actor_id
    event.rejection_reason = None
    await schedule_event_jobs(db, event)
    await write_audit(db, action="event_approved", entity_type="event", entity_id=event.id, actor_id=actor_id)
    await db.flush()
    return event


async def reject_event(db: AsyncSession, event: Event, actor_id, reason: str) -> Event:
    event.status = EventStatus.REJECTED
    event.rejection_reason = reason
    event.reviewed_by = actor_id
    await cancel_event_jobs(db, event.id)
    await write_audit(
        db, action="event_rejected", entity_type="event", entity_id=event.id, actor_id=actor_id, extra={"reason": reason}
    )
    await db.flush()
    return event


async def mark_event_started(db: AsyncSession, event: Event, actor_id) -> Event:
    """The organizer says the match has begun.

    Nothing about a custom is decided by the clock any more: this is what moves
    it out of the upcoming list into "past", and closes joining and ROOM ID /
    PASS entry for good.
    """
    if event.archived_at is not None:
        return event
    now = datetime.now(UTC)
    event.archived_at = now
    event.finished_at = event.finished_at or now
    if event.status in {EventStatus.PUBLISHED, EventStatus.FULL}:
        event.status = EventStatus.STARTED
    await cancel_event_jobs(db, event.id)
    await write_audit(
        db,
        action="event_started_by_organizer",
        entity_type="event",
        entity_id=event.id,
        actor_id=actor_id,
    )
    await db.flush()
    return event


async def cancel_event(db: AsyncSession, event: Event, actor_id, reason: str) -> Event:
    event.status = EventStatus.CANCELLED
    event.cancel_reason = reason
    event.cancelled_at = datetime.now(UTC)
    event.archived_at = event.archived_at or event.cancelled_at
    event.deep_link_active = False
    await cancel_event_jobs(db, event.id)
    await write_audit(
        db, action="event_cancelled", entity_type="event", entity_id=event.id, actor_id=actor_id, extra={"reason": reason}
    )
    await db.flush()
    return event


async def reschedule_event(db: AsyncSession, event: Event, actor_id, starts_at, registration_ends_at, credentials_send_at) -> Event:
    _validate_times(starts_at, registration_ends_at, credentials_send_at)
    before = {
        "starts_at": event.starts_at.isoformat(),
        "registration_ends_at": event.registration_ends_at.isoformat(),
        "credentials_send_at": event.credentials_send_at.isoformat(),
    }
    event.starts_at = as_utc(starts_at)
    event.registration_ends_at = as_utc(registration_ends_at)
    event.credentials_send_at = as_utc(credentials_send_at)
    await cancel_event_jobs(db, event.id)
    if event.status in {EventStatus.PUBLISHED, EventStatus.FULL}:
        await schedule_event_jobs(db, event)
    await write_audit(
        db,
        action="event_rescheduled",
        entity_type="event",
        entity_id=event.id,
        actor_id=actor_id,
        before=before,
        after={
            "starts_at": event.starts_at.isoformat(),
            "registration_ends_at": event.registration_ends_at.isoformat(),
            "credentials_send_at": event.credentials_send_at.isoformat(),
        },
    )
    await db.flush()
    return event


async def update_credentials(db: AsyncSession, event: Event, actor_id, room_id: str, room_password: str) -> RoomCredential:
    creds = await db.scalar(select(RoomCredential).where(RoomCredential.event_id == event.id))
    already_sent = bool(creds and creds.sent_at)
    if creds is None:
        creds = RoomCredential(
            event_id=event.id,
            room_id_encrypted=encrypt_secret(room_id),
            room_password_encrypted=encrypt_secret(room_password),
            last_changed_by=actor_id,
        )
        db.add(creds)
    else:
        creds.room_id_encrypted = encrypt_secret(room_id)
        creds.room_password_encrypted = encrypt_secret(room_password)
        creds.version += 1
        creds.last_changed_by = actor_id
        if already_sent:
            creds.correction_required = True
    await write_audit(
        db,
        action="room_credentials_updated",
        entity_type="event",
        entity_id=event.id,
        actor_id=actor_id,
        extra={"version": creds.version, "correction_required": creds.correction_required},
    )
    await db.flush()
    return creds


async def waiting_live_credential_event(db: AsyncSession, user_id) -> Event | None:
    """کاستومی که زمانش رسیده و هنوز رمز از برگزارکننده نگرفته یا ارسال نشده."""
    now = datetime.now(UTC)
    org = await db.scalar(select(Organizer).where(Organizer.user_id == user_id))
    if not org:
        return None
    rows = (
        await db.scalars(
            select(Event)
            .where(
                Event.organizer_id == org.id,
                Event.deleted_at.is_(None),
                Event.status.in_(
                    [EventStatus.PUBLISHED, EventStatus.FULL, EventStatus.STARTED]
                ),
                Event.archived_at.is_(None),
                Event.starts_at <= now + timedelta(minutes=20),
                Event.starts_at >= now - timedelta(minutes=get_settings().auto_archive_minutes),
            )
            .order_by(Event.starts_at.asc())
        )
    ).all()
    for event in rows:
        creds = await db.scalar(select(RoomCredential).where(RoomCredential.event_id == event.id))
        if creds is None or creds.sent_at is None:
            return event
    return None


async def copy_event(db: AsyncSession, event: Event, organizer: Organizer, actor_id) -> Event:
    data = {
        "title": f"کپی {event.title}"[:160],
        "description": event.description,
        "banner_file_id": event.banner_file_id,
        "starts_at": event.starts_at,
        "registration_ends_at": event.registration_ends_at,
        "credentials_send_at": event.credentials_send_at,
        "timezone": event.timezone,
        "region": event.region,
        "game_mode": event.game_mode,
        "capacity": event.capacity,
        "waitlist_enabled": event.waitlist_enabled,
        "visibility": event.visibility,
        "require_rules_accept": event.require_rules_accept,
        "require_ff_player_id": event.require_ff_player_id,
        "require_profile_complete": event.require_profile_complete,
        "required_referrals": event.required_referrals,
        "rules_text": event.rules_text,
        "winner_method": event.winner_method,
        "custom_credentials_message": event.custom_credentials_message,
        "prize_summary": event.prize_summary,
        "payout_contact": event.payout_contact,
        "social_url": event.social_url,
        "social_platform": event.social_platform,
        "social_note": event.social_note,
        "channel_id": event.channel_id,
        "required_channel_ids": [rc.channel_id for rc in event.required_channels],
        "prizes": [{"place": p.place, "title": p.title, "description": p.description} for p in event.prizes],
        "reminder_offsets_minutes": event.reminder_offsets_minutes or [60, 10],
    }
    copy = await create_event(db, organizer, data, actor_id)
    copy.status = EventStatus.DRAFT
    await db.flush()
    return copy


def public_event_dict(event: Event, include_secrets: bool = False) -> dict:
    assert not include_secrets
    return {
        "id": str(event.id),
        "public_token": event.public_token,
        "title": event.title,
        "description": event.description,
        "banner_file_id": event.banner_file_id,
        "starts_at": event.starts_at.isoformat(),
        "registration_ends_at": event.registration_ends_at.isoformat(),
        "credentials_send_at": event.credentials_send_at.isoformat(),
        "timezone": event.timezone,
        "region": event.region,
        "game_mode": event.game_mode,
        "capacity": event.capacity,
        "confirmed_count": event.confirmed_count,
        "status": event.status,
        "featured": event.featured,
        "prize_summary": event.prize_summary,
        "unlimited_capacity": capacity_is_unlimited(event),
        "archived_at": event.archived_at.isoformat() if event.archived_at else None,
        "payout_contact": event.payout_contact,
        "social_url": event.social_url,
        "social_platform": event.social_platform,
        "visibility": event.visibility,
        "required_referrals": event.required_referrals,
        "waitlist_enabled": event.waitlist_enabled,
        "deep_link": f"https://t.me/{get_settings().bot_username}?start=event_{event.public_token}"
        if get_settings().bot_username
        else None,
    }
