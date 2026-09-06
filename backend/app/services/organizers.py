from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import OrganizerStatus, RoleName, TrustEventType
from app.models.organizer import Organizer, OrganizerTrustEvent
from app.models.user import Role, User, UserRole
from app.services.audit import write_audit


async def get_or_apply(db: AsyncSession, user: User, display_name: str | None = None) -> Organizer:
    from app.services.settings import get_setting

    auto = bool(await get_setting(db, "auto_approve_organizers", True))
    existing = await db.scalar(select(Organizer).where(Organizer.user_id == user.id))
    if existing:
        if existing.status == OrganizerStatus.PENDING and auto:
            await approve_organizer(db, existing, user.id, verified=False)
        return existing
    org = Organizer(
        user_id=user.id,
        status=OrganizerStatus.PENDING,
        display_name=display_name or user.first_name,
        trust_score=50.0,
    )
    db.add(org)
    await write_audit(db, action="organizer_applied", entity_type="organizer", entity_id=org.id, actor_id=user.id)
    await db.flush()
    if auto:
        await approve_organizer(db, org, user.id, verified=False)
    return org


async def approve_organizer(db: AsyncSession, org: Organizer, actor_id, verified: bool = False) -> Organizer:
    org.status = OrganizerStatus.APPROVED
    org.verified_badge = verified
    org.reviewed_by = actor_id
    org.reviewed_at = datetime.now(UTC)
    role = await db.scalar(select(Role).where(Role.name == RoleName.ORGANIZER))
    if role:
        has = await db.scalar(
            select(UserRole).where(UserRole.user_id == org.user_id, UserRole.role_id == role.id)
        )
        if not has:
            db.add(UserRole(user_id=org.user_id, role_id=role.id, granted_by=actor_id))
    apply_trust(db, org, TrustEventType.CHANNEL_VERIFIED, 5, "تأیید برگزارکننده", actor_id)
    await write_audit(db, action="organizer_approved", entity_type="organizer", entity_id=org.id, actor_id=actor_id)
    await db.flush()
    return org


async def reject_organizer(db: AsyncSession, org: Organizer, actor_id, reason: str) -> Organizer:
    org.status = OrganizerStatus.REJECTED
    org.rejection_reason = reason
    org.reviewed_by = actor_id
    org.reviewed_at = datetime.now(UTC)
    await write_audit(
        db, action="organizer_rejected", entity_type="organizer", entity_id=org.id, actor_id=actor_id, extra={"reason": reason}
    )
    await db.flush()
    return org


def apply_trust(db: AsyncSession, org: Organizer, event_type: str, delta: float, reason: str, actor_id=None, related=None) -> None:
    org.trust_score = max(0.0, min(100.0, float(org.trust_score) + delta))
    db.add(
        OrganizerTrustEvent(
            organizer_id=org.id,
            event_type=event_type,
            delta=delta,
            reason=reason,
            related_event_id=related,
            created_by=actor_id,
        )
    )


def explain_trust(org: Organizer, events: list[OrganizerTrustEvent]) -> dict:
    return {
        "score": org.trust_score,
        "note": "امتیاز اعتماد فقط راهنماست و مبنای Ban خودکار نیست.",
        "history": [
            {"type": e.event_type, "delta": e.delta, "reason": e.reason, "at": e.created_at.isoformat()}
            for e in events
        ],
    }


# ------------------------------------------------------------------ public profile


async def organizer_stats(db: AsyncSession, organizer_id) -> dict:
    """The numbers a player would want before trusting a stranger's custom.

    "Delivered" counts customs whose ROOM ID / PASS actually went out, not ones
    that merely finished - that is the promise an organizer is judged on.
    """
    from sqlalchemy import func

    from app.core.enums import EventStatus
    from app.models.event import Event, RoomCredential

    live_statuses = [
        EventStatus.PUBLISHED,
        EventStatus.FULL,
        EventStatus.STARTED,
        EventStatus.FINISHED,
    ]
    held = int(
        await db.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.organizer_id == organizer_id,
                Event.deleted_at.is_(None),
                Event.status.in_(live_statuses),
            )
        )
        or 0
    )
    delivered = int(
        await db.scalar(
            select(func.count())
            .select_from(Event)
            .join(RoomCredential, RoomCredential.event_id == Event.id)
            .where(
                Event.organizer_id == organizer_id,
                Event.deleted_at.is_(None),
                RoomCredential.sent_at.is_not(None),
            )
        )
        or 0
    )
    cancelled = int(
        await db.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.organizer_id == organizer_id,
                Event.deleted_at.is_(None),
                Event.status == EventStatus.CANCELLED,
            )
        )
        or 0
    )
    players = int(
        await db.scalar(
            select(func.coalesce(func.sum(Event.confirmed_count), 0)).where(
                Event.organizer_id == organizer_id,
                Event.deleted_at.is_(None),
                Event.status.in_(live_statuses),
            )
        )
        or 0
    )
    return {
        "held": held,
        "delivered": delivered,
        "cancelled": cancelled,
        "players": players,
    }


async def upcoming_events_for(db: AsyncSession, organizer_id, *, limit: int = 5):
    """Their open customs, for the buttons under the profile."""
    from app.core.enums import EventStatus, EventVisibility
    from app.models.event import Event
    from app.services.event_display import event_public_load_options

    rows = (
        await db.scalars(
            select(Event)
            .where(
                Event.organizer_id == organizer_id,
                Event.deleted_at.is_(None),
                Event.archived_at.is_(None),
                Event.visibility == EventVisibility.PUBLIC,
                Event.deep_link_active.is_(True),
                Event.status.in_([EventStatus.PUBLISHED, EventStatus.FULL, EventStatus.STARTED]),
            )
            .options(*event_public_load_options())
            .order_by(Event.starts_at.asc())
            .limit(limit)
        )
    ).all()
    return list(rows)


def organizer_deep_link(organizer_id) -> str:
    from app.core.config import get_settings

    return f"https://t.me/{get_settings().bot_username}?start=org_{organizer_id}"


async def format_organizer_profile(db: AsyncSession, org: Organizer) -> str:
    """One card a stranger can read before deciding to trust this organizer."""
    from app.bot.helpers import esc
    from app.services.event_display import organizer_public_name
    from app.services.reviews import format_rating_line, review_summary_for_organizer
    from app.services.trust import badge, is_risky

    user = await db.get(User, org.user_id)
    stats = await organizer_stats(db, org.id)
    rating = format_rating_line(
        await review_summary_for_organizer(db, org.id), prefix="امتیاز از بازیکن‌ها"
    )
    verified = " ✅" if org.verified_badge else ""
    lines = [
        f"👑 <b>{esc(organizer_public_name(org, user))}</b>{verified}",
        "━━━━━━━━━━━━━━",
        f"🛡 اعتبار: {badge(org.trust_score)} ({int(org.trust_score or 0)}/100)",
        rating,
        "",
        f"🎮 کاستوم برگزار کرده: <b>{stats['held']}</b>",
        f"🆔 ROOM ID / PASS فرستاده: <b>{stats['delivered']}</b>",
        f"👥 مجموع شرکت‌کننده: <b>{stats['players']}</b>",
    ]
    if stats["cancelled"]:
        lines.append(f"❌ لغو کرده: {stats['cancelled']}")
    if (org.bio or "").strip():
        lines.append("")
        lines.append(f"📝 {esc(org.bio.strip())}")
    if is_risky(org):
        lines.append("")
        lines.append("⚠️ <b>اعتبار این برگزارکننده پایین است. با احتیاط شرکت کنید.</b>")
    return "\n".join(line for line in lines if line is not None)
