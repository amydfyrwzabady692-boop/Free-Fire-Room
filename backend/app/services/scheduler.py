from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.enums import JobStatus, JobType
from app.models.event import Event
from app.models.jobs import ScheduledJob


def _key(job_type: str, event_id: UUID, extra: str = "") -> str:
    return f"{job_type}:{event_id}:{extra}"


async def schedule_event_jobs(db: AsyncSession, event: Event) -> None:
    offsets = event.reminder_offsets_minutes or [60, 15, 5]
    jobs = [
        (JobType.SEND_CREDENTIALS, event.credentials_send_at, "creds"),
        (JobType.EVENT_START, event.starts_at, "start"),
        (JobType.EVENT_FINISH, event.starts_at + timedelta(hours=3), "finish"),
        (JobType.PURGE_CREDENTIALS, event.starts_at + timedelta(days=7), "purge"),
        (JobType.RECHECK_REQUIREMENTS, event.credentials_send_at - timedelta(minutes=20), "recheck"),
    ]
    for minutes in offsets:
        run_at = event.starts_at - timedelta(minutes=int(minutes))
        if run_at > datetime.now(UTC):
            jobs.append((JobType.REMINDER, run_at, f"rem{minutes}"))

    for job_type, run_at, extra in jobs:
        if run_at < datetime.now(UTC) - timedelta(minutes=1) and job_type != JobType.SEND_CREDENTIALS:
            continue
        existing = await db.scalar(
            select(ScheduledJob).where(ScheduledJob.idempotency_key == _key(job_type, event.id, extra))
        )
        if existing:
            if existing.status in {JobStatus.PENDING, JobStatus.FAILED}:
                existing.run_at = run_at
                existing.status = JobStatus.PENDING
            continue
        db.add(
            ScheduledJob(
                job_type=job_type,
                entity_type="event",
                entity_id=event.id,
                run_at=run_at,
                status=JobStatus.PENDING,
                idempotency_key=_key(job_type, event.id, extra),
                payload={"offset": extra},
            )
        )
    await db.flush()


async def cancel_event_jobs(db: AsyncSession, event_id: UUID) -> int:
    rows = (
        await db.scalars(
            select(ScheduledJob).where(
                ScheduledJob.entity_id == event_id,
                ScheduledJob.status.in_([JobStatus.PENDING, JobStatus.FAILED]),
            )
        )
    ).all()
    for row in rows:
        row.status = JobStatus.CANCELLED
    await db.flush()
    return len(rows)


def cancel_event_jobs_sync(db: Session, event_id: UUID) -> int:
    rows = db.scalars(
        select(ScheduledJob).where(
            ScheduledJob.entity_id == event_id,
            ScheduledJob.status.in_([JobStatus.PENDING, JobStatus.FAILED]),
        )
    ).all()
    for row in rows:
        row.status = JobStatus.CANCELLED
    db.flush()
    return len(rows)


def schedule_event_jobs_sync(db: Session, event: Event) -> None:
    offsets = event.reminder_offsets_minutes or [60, 15, 5]
    jobs = [
        (JobType.SEND_CREDENTIALS, event.credentials_send_at, "creds"),
        (JobType.EVENT_START, event.starts_at, "start"),
        (JobType.RECHECK_REQUIREMENTS, event.credentials_send_at - timedelta(minutes=20), "recheck"),
    ]
    for minutes in offsets:
        run_at = event.starts_at - timedelta(minutes=int(minutes))
        jobs.append((JobType.REMINDER, run_at, f"rem{minutes}"))
    for job_type, run_at, extra in jobs:
        key = _key(job_type, event.id, extra)
        existing = db.scalar(select(ScheduledJob).where(ScheduledJob.idempotency_key == key))
        if existing:
            existing.run_at = run_at
            if existing.status != JobStatus.DONE:
                existing.status = JobStatus.PENDING
            continue
        db.add(
            ScheduledJob(
                job_type=job_type,
                entity_type="event",
                entity_id=event.id,
                run_at=run_at,
                status=JobStatus.PENDING,
                idempotency_key=key,
                payload={"offset": extra},
            )
        )
    db.flush()


def claim_due_jobs_sync(db: Session, worker_id: str, limit: int = 20) -> list[ScheduledJob]:
    now = datetime.now(UTC)
    rows = db.scalars(
        select(ScheduledJob)
        .where(ScheduledJob.status == JobStatus.PENDING, ScheduledJob.run_at <= now)
        .order_by(ScheduledJob.run_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    claimed = []
    for row in rows:
        row.status = JobStatus.RUNNING
        row.locked_at = now
        row.locked_by = worker_id
        row.attempts += 1
        claimed.append(row)
    db.flush()
    return claimed
