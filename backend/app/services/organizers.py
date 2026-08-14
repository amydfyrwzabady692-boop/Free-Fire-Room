from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import OrganizerStatus, RoleName, TrustEventType
from app.core.errors import ConflictError, ValidationAppError
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
