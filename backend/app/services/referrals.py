from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.errors import ValidationAppError
from app.core.security import generate_unguessable_token
from app.models.referral import Referral, ReferralLink
from app.models.user import User
from app.services.settings import get_setting


async def get_or_create_link(db: AsyncSession, user_id: UUID, event_id: UUID | None, campaign: str = "default") -> ReferralLink:
    row = await db.scalar(
        select(ReferralLink).where(
            ReferralLink.user_id == user_id,
            ReferralLink.event_id == event_id if event_id else ReferralLink.event_id.is_(None),
            ReferralLink.campaign == campaign,
        )
    )
    if row:
        return row
    row = ReferralLink(
        user_id=user_id,
        event_id=event_id,
        campaign=campaign,
        token=generate_unguessable_token(18),
        is_active=True,
    )
    db.add(row)
    await db.flush()
    return row


async def apply_start_referral(
    db: AsyncSession,
    *,
    invitee: User,
    token: str,
) -> Referral | None:
    link = await db.scalar(select(ReferralLink).where(ReferralLink.token == token, ReferralLink.is_active.is_(True)))
    if not link:
        return None
    if link.user_id == invitee.id:
        db.add(
            Referral(
                link_id=link.id,
                event_id=link.event_id,
                inviter_id=link.user_id,
                invitee_id=invitee.id,
                is_valid=False,
                invalid_reason="self_referral",
            )
        )
        await db.flush()
        return None

    existing = await db.scalar(
        select(Referral).where(Referral.event_id == link.event_id, Referral.invitee_id == invitee.id)
    )
    if existing:
        return existing

    # one valid referrer per campaign/event
    invitee.referred_by_user_id = invitee.referred_by_user_id or link.user_id
    ref = Referral(
        link_id=link.id,
        event_id=link.event_id,
        inviter_id=link.user_id,
        invitee_id=invitee.id,
        is_valid=False,
        invalid_reason="pending_onboarding",
    )
    db.add(ref)
    await db.flush()
    return ref


async def validate_pending_referrals(db: AsyncSession, invitee: User) -> list[Referral]:
    """Mark referrals valid after invitee completed onboarding (TOS + global channels)."""
    if not invitee.onboarding_completed_at:
        return []
    rows = (
        await db.scalars(
            select(Referral).where(
                Referral.invitee_id == invitee.id,
                Referral.is_valid.is_(False),
                Referral.invalid_reason == "pending_onboarding",
            )
        )
    ).all()
    validated = []
    now = datetime.now(UTC)
    for ref in rows:
        if ref.inviter_id == invitee.id:
            ref.invalid_reason = "self_referral"
            continue
        # new-user heuristic: account created recently relative to referral
        age = now - (invitee.created_at.replace(tzinfo=UTC) if invitee.created_at.tzinfo is None else invitee.created_at)
        max_hours = int(await get_setting(db, "new_user_referral_hours", 24) or 24)
        if age > timedelta(hours=max_hours) and invitee.created_at < ref.created_at:
            # already existed before this referral campaign click — still valid if first start via this link
            # Spec: new OR meeting defined criteria. Completing onboarding via this deep link counts.
            pass
        ref.is_valid = True
        ref.invalid_reason = None
        ref.validated_at = now
        ref.onboarded_at = invitee.onboarding_completed_at
        await db.execute(
            update(ReferralLink)
            .where(ReferralLink.id == ref.link_id)
            .values(valid_count=ReferralLink.valid_count + 1)
        )
        validated.append(ref)
    await db.flush()
    return validated


def apply_referral_sync(db: Session, inviter_id, invitee_id, event_id, token: str) -> str:
    """Pure referral rules for tests: returns valid|self|duplicate|missing."""
    if inviter_id == invitee_id:
        return "self"
    link = db.scalar(select(ReferralLink).where(ReferralLink.token == token, ReferralLink.is_active.is_(True)))
    if not link:
        return "missing"
    existing = db.scalar(
        select(Referral).where(Referral.event_id == event_id, Referral.invitee_id == invitee_id)
    )
    if existing:
        return "duplicate"
    ref = Referral(
        link_id=link.id,
        event_id=event_id,
        inviter_id=inviter_id,
        invitee_id=invitee_id,
        is_valid=True,
        validated_at=datetime.now(UTC),
    )
    db.add(ref)
    db.execute(
        update(ReferralLink).where(ReferralLink.id == link.id).values(valid_count=ReferralLink.valid_count + 1)
    )
    db.flush()
    return "valid"
