from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload

from app.bot.helpers import esc
from app.core.enums import SocialPlatform, SocialProofStatus
from app.core.errors import ConflictError, ValidationAppError
from app.models.event import Event
from app.models.social import SocialProof
from app.models.user import User

PLATFORM_FA = {
    SocialPlatform.INSTAGRAM: "اینستاگرام",
    SocialPlatform.YOUTUBE: "یوتیوب",
    SocialPlatform.OTHER: "پیج برگزارکننده",
}


def detect_platform(url: str) -> str:
    low = (url or "").lower()
    if "instagram." in low or low.lstrip("@").startswith("instagram"):
        return SocialPlatform.INSTAGRAM
    if "youtube." in low or "youtu.be" in low:
        return SocialPlatform.YOUTUBE
    return SocialPlatform.OTHER


def normalize_social_url(raw: str) -> tuple[str, str]:
    """Accept a link, an @handle, or a bare username. Returns (url, platform)."""
    text = (raw or "").strip()
    if not text:
        raise ValidationAppError("social_url", "آدرس پیج را بفرستید.")
    if len(text) > 300:
        raise ValidationAppError("social_url", "آدرس خیلی بلند است.")
    if text.startswith("@"):
        handle = text.lstrip("@").strip("/")
        if not handle:
            raise ValidationAppError("social_url", "آیدی پیج را کامل بفرستید.")
        return f"https://instagram.com/{handle}", SocialPlatform.INSTAGRAM
    if not text.startswith(("http://", "https://")):
        if "." in text and " " not in text:
            text = "https://" + text
        else:
            raise ValidationAppError(
                "social_url",
                "لینک درست نیست. یک لینک کامل مثل https://instagram.com/yourpage بفرستید، یا «رد کردن» را بزنید.",
            )
    return text[:300], detect_platform(text)


def social_required(event: Event) -> bool:
    return bool((getattr(event, "social_url", None) or "").strip())


def platform_label(event: Event) -> str:
    return PLATFORM_FA.get(getattr(event, "social_platform", None) or SocialPlatform.OTHER, "پیج برگزارکننده")


def format_social_step(event: Event) -> str:
    """What the player is told to do at the last gate."""
    if not social_required(event):
        return ""
    note = (getattr(event, "social_note", None) or "").strip()
    body = (
        f"📸 <b>مرحله آخر: فالو {platform_label(event)}</b>\n"
        f"{esc(event.social_url)}\n\n"
        "۱) لینک بالا را باز کنید و پیج را فالو کنید.\n"
        "۲) از فالو کردنتان اسکرین‌شات بگیرید.\n"
        "۳) همان اسکرین‌شات را همین‌جا بفرستید.\n\n"
        "برگزارکننده اسکرین را می‌بیند و بعد از تأیید او ثبت‌نام شما قطعی می‌شود "
        "و سر ساعت ROOM ID و PASS برایتان می‌آید."
    )
    if note:
        body += f"\n\n📝 {esc(note)}"
    return body


async def get_proof(db: AsyncSession, *, event_id, user_id) -> SocialProof | None:
    return await db.scalar(
        select(SocialProof).where(SocialProof.event_id == event_id, SocialProof.user_id == user_id)
    )


def get_proof_sync(db: Session, *, event_id, user_id) -> SocialProof | None:
    return db.scalar(
        select(SocialProof).where(SocialProof.event_id == event_id, SocialProof.user_id == user_id)
    )


def social_gate_ok_sync(db: Session, event: Event, user: User) -> bool:
    """Used at ROOM ID / PASS send time, where the session is synchronous."""
    if not social_required(event):
        return True
    proof = get_proof_sync(db, event_id=event.id, user_id=user.id)
    return bool(proof and proof.status == SocialProofStatus.APPROVED)


async def submit_proof(db: AsyncSession, *, event: Event, user: User, file_id: str) -> SocialProof:
    if not social_required(event):
        raise ValidationAppError("social_not_required", "این کاستوم شرط فالو ندارد.")
    proof = await get_proof(db, event_id=event.id, user_id=user.id)
    if proof and proof.status == SocialProofStatus.APPROVED:
        raise ConflictError("social_already_approved", "اسکرین شما قبلاً تأیید شده است.")
    if proof:
        proof.file_id = file_id
        proof.status = SocialProofStatus.PENDING
        proof.reviewed_by = None
        proof.reviewed_at = None
        proof.review_note = None
    else:
        proof = SocialProof(
            event_id=event.id,
            user_id=user.id,
            file_id=file_id,
            status=SocialProofStatus.PENDING,
        )
        db.add(proof)
    await db.flush()
    return proof


async def review_proof(
    db: AsyncSession,
    proof: SocialProof,
    *,
    approved: bool,
    reviewer_id,
    note: str | None = None,
) -> SocialProof:
    proof.status = SocialProofStatus.APPROVED if approved else SocialProofStatus.REJECTED
    proof.reviewed_by = reviewer_id
    proof.reviewed_at = datetime.now(UTC)
    proof.review_note = note
    await db.flush()
    return proof


async def pending_proofs_for_event(db: AsyncSession, event_id, *, limit: int = 60) -> list[SocialProof]:
    rows = (
        await db.scalars(
            select(SocialProof)
            .where(SocialProof.event_id == event_id, SocialProof.status == SocialProofStatus.PENDING)
            .options(selectinload(SocialProof.user))
            .order_by(SocialProof.created_at.asc())
            .limit(limit)
        )
    ).all()
    return list(rows)


async def pending_proof_count(db: AsyncSession, event_id) -> int:
    from sqlalchemy import func

    return int(
        await db.scalar(
            select(func.count())
            .select_from(SocialProof)
            .where(SocialProof.event_id == event_id, SocialProof.status == SocialProofStatus.PENDING)
        )
        or 0
    )
