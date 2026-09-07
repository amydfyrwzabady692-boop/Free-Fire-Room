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

#: What a screenshot's status means to the organizer. Nothing here says
#: "waiting": sending the screenshot is what counts, so an unreviewed one is
#: simply one nobody has looked at yet.
PROOF_STATUS_FA = {
    SocialProofStatus.PENDING: "بررسی‌نشده",
    SocialProofStatus.APPROVED: "بررسی و تأیید شده",
    SocialProofStatus.REJECTED: "رد شده",
}

#: how many pages one custom may ask a player to follow
MAX_SOCIAL_TASKS = 5


def proof_status_label(status: str | None) -> str:
    return PROOF_STATUS_FA.get(status or SocialProofStatus.PENDING, "بررسی‌نشده")


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
        "همین که اسکرین را بفرستید ثبت‌نامتان قطعی است — منتظر تأیید کسی نمی‌مانید. "
        "سر ساعت ROOM ID و PASS همین‌جا برایتان می‌آید؛ تا آن لحظه در کانال‌ها بمانید."
    )
    lines.append("فقط اگر اسکرین بی‌ربط باشد برگزارکننده می‌تواند ردش کند و باید دوباره بفرستید.")
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


async def proofs_with_tasks_for_user(db: AsyncSession, *, event_id, user_id) -> list[SocialProof]:
    """Every screenshot this player sent for this custom, page included.

    Ordered by the organizer's own page order, not by when the rows landed:
    a player who sends three screenshots in one burst gives them all the same
    ``created_at``, and ordering on that alone leaves the tiebreak to a random
    UUID - so "page 1 of 3" would name a different page on every read.

    ``selectinload`` is not optional either: the winner bundle reads
    ``proof.task.url`` to label each screenshot, and a lazy load on an
    AsyncSession raises MissingGreenlet.
    """
    rows = (
        await db.scalars(
            select(SocialProof)
            .where(SocialProof.event_id == event_id, SocialProof.user_id == user_id)
            .options(selectinload(SocialProof.task))
        )
    ).all()
    return sorted(
        rows,
        key=lambda p: (
            p.task.sort_order if p.task is not None else -1,
            p.created_at or datetime.min.replace(tzinfo=UTC),
        ),
    )


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


def gate_from(tasks: list[EventSocialTask], proofs: list[SocialProof]) -> bool:
    """Has this player done what the follow gate asks?

    Sending the screenshot is the whole requirement - nobody has to approve it.
    The only thing that closes the gate again is the organizer rejecting a
    screenshot, which is why the predicate is "not rejected" rather than
    "approved". Both session flavours call this so they cannot drift apart.
    """
    ok = {p.task_id for p in proofs if p.status != SocialProofStatus.REJECTED}
    if not tasks:
        # a custom from before the multi-page change, whose backfill has not
        # run: one screenshot, no task row to hang it on
        return bool(ok)
    return all(task.id in ok for task in tasks)


def rejected_proof(proofs: list[SocialProof]) -> SocialProof | None:
    for proof in proofs:
        if proof.status == SocialProofStatus.REJECTED:
            return proof
    return None


def social_gate_ok_sync(db: Session, event: Event, user: User) -> bool:
    """Used at ROOM ID / PASS send time, where the session is synchronous."""
    if not social_required(event):
        return True
    tasks = list_tasks_sync(db, event.id)
    proofs = list(
        db.scalars(
            select(SocialProof).where(
                SocialProof.event_id == event.id, SocialProof.user_id == user.id
            )
        ).all()
    )
    return gate_from(tasks, proofs)


async def social_gate_ok(db: AsyncSession, event: Event, user: User) -> bool:
    if not social_required(event):
        return True
    tasks = await list_tasks(db, event.id)
    proofs = await proofs_for_user(db, event_id=event.id, user_id=user.id)
    return gate_from(tasks, proofs)


async def submit_proof(
    db: AsyncSession, *, event: Event, user: User, file_id: str, task: EventSocialTask | None
) -> SocialProof:
    if not social_required(event):
        raise ValidationAppError("social_not_required", "این کاستوم شرط فالو ندارد.")
    task_id = task.id if task is not None else None
    proof = await get_proof(db, event_id=event.id, user_id=user.id, task_id=task_id)
    if proof and proof.status == SocialProofStatus.APPROVED:
        raise ConflictError("social_already_approved", "اسکرین این پیج قبلاً ثبت شده است.")
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


async def proofs_for_event(
    db: AsyncSession,
    event_id,
    *,
    statuses: list[str] | None = None,
    limit: int = 5,
    offset: int = 0,
) -> list[SocialProof]:
    """One page of an organizer's screenshot archive.

    Paged in the database, not in Python: a busy custom can hold hundreds of
    screenshots and the panel only ever shows a handful. The ``id`` tiebreak
    matters - ``created_at`` alone is not unique, and offset paging over a
    non-unique order silently repeats and skips rows.
    """
    stmt = select(SocialProof).where(SocialProof.event_id == event_id)
    if statuses:
        stmt = stmt.where(SocialProof.status.in_(statuses))
    rows = (
        await db.scalars(
            stmt.options(selectinload(SocialProof.user), selectinload(SocialProof.task))
            .order_by(SocialProof.created_at.desc(), SocialProof.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return list(rows)


async def proof_counts_for_event(db: AsyncSession, event_id) -> dict:
    """{total, pending, approved, rejected} in one grouped query."""
    rows = (
        await db.execute(
            select(SocialProof.status, func.count())
            .where(SocialProof.event_id == event_id)
            .group_by(SocialProof.status)
        )
    ).all()
    counts = {"total": 0, "pending": 0, "approved": 0, "rejected": 0}
    for status, n in rows:
        counts[str(status)] = counts.get(str(status), 0) + int(n or 0)
        counts["total"] += int(n or 0)
    return counts


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
