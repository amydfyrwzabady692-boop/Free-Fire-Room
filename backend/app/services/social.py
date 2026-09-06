from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload

from app.bot.helpers import esc
from app.core.enums import SocialPlatform, SocialProofStatus
from app.core.errors import ConflictError, ValidationAppError
from app.models.event import Event
from app.models.social import EventSocialTask, SocialProof
from app.models.user import User

PLATFORM_FA = {
    SocialPlatform.INSTAGRAM: "اینستاگرام",
    SocialPlatform.YOUTUBE: "یوتیوب",
    SocialPlatform.OTHER: "پیج برگزارکننده",
}

#: how many pages one custom may ask a player to follow
MAX_SOCIAL_TASKS = 5


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
    """Cheap column check, kept in sync with the task rows on create.

    ``events.social_url`` holds the first page so this stays a plain attribute
    read on both sync and async sessions; the full list lives in
    ``event_social_tasks``.
    """
    return bool((getattr(event, "social_url", None) or "").strip())


def platform_label(platform: str | None) -> str:
    return PLATFORM_FA.get(platform or SocialPlatform.OTHER, "پیج برگزارکننده")


def task_label(task: EventSocialTask | None) -> str:
    if task is None:
        return "پیج برگزارکننده"
    return platform_label(task.platform)


# ------------------------------------------------------------------ tasks


async def list_tasks(db: AsyncSession, event_id) -> list[EventSocialTask]:
    rows = (
        await db.scalars(
            select(EventSocialTask)
            .where(EventSocialTask.event_id == event_id, EventSocialTask.is_active.is_(True))
            .order_by(EventSocialTask.sort_order.asc(), EventSocialTask.created_at.asc())
        )
    ).all()
    return list(rows)


def list_tasks_sync(db: Session, event_id) -> list[EventSocialTask]:
    rows = db.scalars(
        select(EventSocialTask)
        .where(EventSocialTask.event_id == event_id, EventSocialTask.is_active.is_(True))
        .order_by(EventSocialTask.sort_order.asc(), EventSocialTask.created_at.asc())
    ).all()
    return list(rows)


def seed_tasks(db, event: Event, pages: list[dict]) -> None:
    """Write the organizer's page list. Called inside create_event."""
    for i, page in enumerate(pages[:MAX_SOCIAL_TASKS]):
        url = (page.get("url") or "").strip()
        if not url:
            continue
        db.add(
            EventSocialTask(
                event_id=event.id,
                url=url,
                platform=page.get("platform") or detect_platform(url),
                sort_order=i,
                is_active=True,
            )
        )


def format_social_step(event: Event, tasks: list[EventSocialTask], done: int = 0) -> str:
    """What the player is told to do at the last gate."""
    if not tasks:
        return ""
    total = len(tasks)
    head = "📸 <b>مرحله آخر: فالو کردن</b>"
    if total > 1:
        head += f"\n{done} از {total} پیج انجام شده."
    lines = [head, ""]
    for i, task in enumerate(tasks, start=1):
        mark = "✅" if i <= done else f"{i})"
        lines.append(f"{mark} {platform_label(task.platform)} — {esc(task.url)}")
    lines.append("")
    lines.append("هر پیج را باز کنید، فالو کنید، اسکرین بگیرید و همین‌جا بفرستید.")
    if total > 1:
        lines.append("برای هر پیج یک اسکرین جدا لازم است.")
    lines.append("")
    lines.append(
        "برگزارکننده اسکرین‌ها را می‌بیند و بعد از تأیید او ثبت‌نام شما قطعی می‌شود "
        "و سر ساعت ROOM ID و PASS برایتان می‌آید."
    )
    note = (getattr(event, "social_note", None) or "").strip()
    if note:
        lines.append(f"\n📝 {esc(note)}")
    return "\n".join(lines)


# ------------------------------------------------------------------ proofs


async def proofs_for_user(db: AsyncSession, *, event_id, user_id) -> list[SocialProof]:
    rows = (
        await db.scalars(
            select(SocialProof).where(
                SocialProof.event_id == event_id, SocialProof.user_id == user_id
            )
        )
    ).all()
    return list(rows)


async def get_proof(db: AsyncSession, *, event_id, user_id, task_id=None) -> SocialProof | None:
    stmt = select(SocialProof).where(
        SocialProof.event_id == event_id, SocialProof.user_id == user_id
    )
    if task_id is not None:
        stmt = stmt.where(SocialProof.task_id == task_id)
    return await db.scalar(stmt)


def get_proof_sync(db: Session, *, event_id, user_id, task_id=None) -> SocialProof | None:
    stmt = select(SocialProof).where(
        SocialProof.event_id == event_id, SocialProof.user_id == user_id
    )
    if task_id is not None:
        stmt = stmt.where(SocialProof.task_id == task_id)
    return db.scalar(stmt)


async def next_task_for(
    db: AsyncSession, *, event: Event, user: User
) -> tuple[EventSocialTask | None, int, int]:
    """(the page still owed, how many are done, how many there are).

    "Done" counts a page whose screenshot is waiting for review too - the
    player has nothing left to do there.
    """
    tasks = await list_tasks(db, event.id)
    if not tasks:
        return None, 0, 0
    proofs = {p.task_id: p for p in await proofs_for_user(db, event_id=event.id, user_id=user.id)}
    done = 0
    pending: EventSocialTask | None = None
    for task in tasks:
        proof = proofs.get(task.id)
        if proof and proof.status in {SocialProofStatus.APPROVED, SocialProofStatus.PENDING}:
            done += 1
            continue
        if pending is None:
            pending = task
    return pending, done, len(tasks)


def social_gate_ok_sync(db: Session, event: Event, user: User) -> bool:
    """Used at ROOM ID / PASS send time, where the session is synchronous."""
    if not social_required(event):
        return True
    tasks = list_tasks_sync(db, event.id)
    if not tasks:
        # a custom from before the multi-page change, whose backfill has not run
        proof = get_proof_sync(db, event_id=event.id, user_id=user.id)
        return bool(proof and proof.status == SocialProofStatus.APPROVED)
    approved = {
        p.task_id
        for p in db.scalars(
            select(SocialProof).where(
                SocialProof.event_id == event.id,
                SocialProof.user_id == user.id,
                SocialProof.status == SocialProofStatus.APPROVED,
            )
        ).all()
    }
    return all(task.id in approved for task in tasks)


async def social_gate_ok(db: AsyncSession, event: Event, user: User) -> bool:
    if not social_required(event):
        return True
    tasks = await list_tasks(db, event.id)
    proofs = await proofs_for_user(db, event_id=event.id, user_id=user.id)
    approved = {p.task_id for p in proofs if p.status == SocialProofStatus.APPROVED}
    if not tasks:
        return bool(approved)
    return all(task.id in approved for task in tasks)


async def submit_proof(
    db: AsyncSession, *, event: Event, user: User, file_id: str, task: EventSocialTask | None
) -> SocialProof:
    if not social_required(event):
        raise ValidationAppError("social_not_required", "این کاستوم شرط فالو ندارد.")
    task_id = task.id if task is not None else None
    proof = await get_proof(db, event_id=event.id, user_id=user.id, task_id=task_id)
    if proof and proof.status == SocialProofStatus.APPROVED:
        raise ConflictError("social_already_approved", "اسکرین این پیج قبلاً تأیید شده است.")
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
            task_id=task_id,
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
            .options(selectinload(SocialProof.user), selectinload(SocialProof.task))
            .order_by(SocialProof.created_at.asc())
            .limit(limit)
        )
    ).all()
    return list(rows)


async def pending_proof_count(db: AsyncSession, event_id) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(SocialProof)
            .where(SocialProof.event_id == event_id, SocialProof.status == SocialProofStatus.PENDING)
        )
        or 0
    )


async def social_done_count(db: AsyncSession, *, event_id, user_id) -> int:
    """Pages this player has nothing left to do on (sent or already approved)."""
    return int(
        await db.scalar(
            select(func.count())
            .select_from(SocialProof)
            .where(
                SocialProof.event_id == event_id,
                SocialProof.user_id == user_id,
                SocialProof.status.in_([SocialProofStatus.APPROVED, SocialProofStatus.PENDING]),
            )
        )
        or 0
    )
