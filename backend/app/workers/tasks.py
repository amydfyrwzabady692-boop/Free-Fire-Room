from __future__ import annotations

import asyncio
import os
import socket
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from app.core.enums import BroadcastStatus, DeliveryStatus, EventStatus, JobStatus, JobType
from app.core.logging import get_logger
from app.core.session import SyncSessionLocal
from app.models.broadcast import BroadcastCampaign, BroadcastDelivery
from app.models.channel import Channel
from app.models.event import Event, RoomCredential
from app.models.jobs import ScheduledJob
from app.models.registration import Registration
from app.models.user import User
from app.services.credentials import deliver_one
from app.services.scheduler import claim_due_jobs_sync
from app.workers.celery_app import celery_app

log = get_logger("worker")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if asyncio.get_event_loop().is_running() else asyncio.run(coro)


@celery_app.task(name="app.workers.tasks.dispatch_due_jobs", bind=True, max_retries=0)
def dispatch_due_jobs(self):
    db = SyncSessionLocal()
    try:
        jobs = claim_due_jobs_sync(db, WORKER_ID, limit=25)
        db.commit()
        for job in jobs:
            try:
                _handle_job(db, job)
                db.commit()
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                job = db.get(ScheduledJob, job.id)
                if job:
                    job.last_error = str(exc)
                    if job.attempts >= job.max_attempts:
                        job.status = JobStatus.FAILED
                    else:
                        job.status = JobStatus.PENDING
                        job.run_at = datetime.now(UTC) + timedelta(seconds=min(300, 2 ** job.attempts))
                    db.commit()
                log.exception("job_failed", job_id=str(job.id) if job else None)
    finally:
        db.close()


def _handle_job(db, job: ScheduledJob) -> None:
    if job.job_type == JobType.SEND_CREDENTIALS:
        _run(_send_credentials(db, job))
        return
    if job.job_type == JobType.REMINDER:
        _run(_send_reminders(db, job))
        job.status = JobStatus.DONE
        job.completed_at = datetime.now(UTC)
        return
    if job.job_type == JobType.EVENT_START:
        event = db.get(Event, job.entity_id)
        if event and event.status not in {EventStatus.CANCELLED, EventStatus.FINISHED}:
            event.status = EventStatus.STARTED
        job.status = JobStatus.DONE
        job.completed_at = datetime.now(UTC)
        return
    if job.job_type == JobType.EVENT_FINISH:
        event = db.get(Event, job.entity_id)
        if event and event.status != EventStatus.CANCELLED:
            event.status = EventStatus.FINISHED
            event.finished_at = datetime.now(UTC)
        job.status = JobStatus.DONE
        job.completed_at = datetime.now(UTC)
        return
    if job.job_type == JobType.PURGE_CREDENTIALS:
        creds = db.scalar(select(RoomCredential).where(RoomCredential.event_id == job.entity_id))
        if creds and not creds.purged_at:
            creds.room_id_encrypted = "purged"
            creds.room_password_encrypted = "purged"
            creds.purged_at = datetime.now(UTC)
        job.status = JobStatus.DONE
        job.completed_at = datetime.now(UTC)
        return
    if job.job_type == JobType.RECHECK_REQUIREMENTS:
        job.status = JobStatus.DONE
        job.completed_at = datetime.now(UTC)
        return
    job.status = JobStatus.DONE
    job.completed_at = datetime.now(UTC)


async def _send_credentials(db, job: ScheduledJob) -> None:
    from app.bot.loader import get_bot
    from redis import Redis
    from app.core.config import get_settings

    bot = get_bot()
    event = db.get(Event, job.entity_id)
    if not event or event.status in {EventStatus.CANCELLED, EventStatus.REJECTED, EventStatus.DRAFT}:
        job.status = JobStatus.CANCELLED
        return
    creds = db.scalar(select(RoomCredential).where(RoomCredential.event_id == event.id))
    if not creds:
        job.status = JobStatus.FAILED
        job.last_error = "missing_credentials"
        return
    redis = Redis.from_url(get_settings().redis_url)
    lock_key = f"lock:creds:{event.id}:{creds.version}"
    if not redis.set(lock_key, WORKER_ID, nx=True, ex=120):
        # another worker owns it; if already done, mark done
        if job.status == JobStatus.RUNNING:
            job.status = JobStatus.PENDING
        return
    try:
        regs = db.scalars(select(Registration).where(Registration.event_id == event.id, Registration.status == "confirmed")).all()
        sent = failed = skipped = 0
        for reg in regs:
            user = db.get(User, reg.user_id)
            if not user:
                skipped += 1
                continue
            result = await deliver_one(bot, db, event, user, creds, job, redis)
            if result == "sent":
                sent += 1
            elif result == "already":
                skipped += 1
            elif result == "skipped":
                skipped += 1
                if event.confirmed_count > 0:
                    event.confirmed_count -= 1
            else:
                failed += 1
                await asyncio.sleep(min(8, 0.05 * (failed + 1)))
        creds.sent_at = datetime.now(UTC)
        event.status = EventStatus.STARTED
        job.status = JobStatus.DONE
        job.completed_at = datetime.now(UTC)
        log.info("credentials_sent", event_id=str(event.id), sent=sent, failed=failed, skipped=skipped)
    finally:
        redis.delete(lock_key)


async def _send_reminders(db, job: ScheduledJob) -> None:
    from app.bot.loader import get_bot

    bot = get_bot()
    event = db.get(Event, job.entity_id)
    if not event:
        return
    regs = db.scalars(select(Registration).where(Registration.event_id == event.id, Registration.status == "confirmed")).all()
    text = f"یادآوری: کاستوم «{event.title}» به‌زودی شروع می‌شود."
    for reg in regs:
        user = db.get(User, reg.user_id)
        if not user or user.is_bot_blocked:
            continue
        try:
            await bot.send_message(user.telegram_id, text)
        except Exception:
            continue
        await asyncio.sleep(0.04)


@celery_app.task(name="app.workers.tasks.run_broadcast", bind=True)
def run_broadcast(self, campaign_id: str):
    db = SyncSessionLocal()
    try:
        _run(_broadcast(db, UUID(campaign_id)))
        db.commit()
    finally:
        db.close()


async def _broadcast(db, campaign_id: UUID) -> None:
    from app.bot.loader import get_bot

    camp = db.get(BroadcastCampaign, campaign_id)
    if not camp or camp.status not in {BroadcastStatus.RUNNING, BroadcastStatus.SCHEDULED, "running", "scheduled"}:
        return
    camp.status = BroadcastStatus.RUNNING
    camp.started_at = datetime.now(UTC)
    users = db.scalars(select(User).where(User.deleted_at.is_(None), User.is_bot_blocked.is_(False))).all()
    bot = get_bot()
    for user in users:
        if camp.status == BroadcastStatus.PAUSED:
            break
        try:
            await bot.send_message(user.telegram_id, camp.body)
            db.add(BroadcastDelivery(campaign_id=camp.id, user_id=user.id, status=DeliveryStatus.SENT, sent_at=datetime.now(UTC)))
            camp.sent_count += 1
        except Exception as exc:  # noqa: BLE001
            db.add(BroadcastDelivery(campaign_id=camp.id, user_id=user.id, status=DeliveryStatus.FAILED, error_message=str(exc)))
            camp.fail_count += 1
        await asyncio.sleep(1 / max(get_outbound_rate(), 1))
    camp.status = BroadcastStatus.DONE
    camp.finished_at = datetime.now(UTC)


def get_outbound_rate() -> int:
    from app.core.config import get_settings

    return get_settings().telegram_outbound_per_second


@celery_app.task(name="app.workers.tasks.recheck_channel_admin")
def recheck_channel_admin():
    db = SyncSessionLocal()
    try:
        _run(_recheck(db))
        db.commit()
    finally:
        db.close()


async def _recheck(db) -> None:
    from app.bot.loader import get_bot
    from app.services.telegram_ops import inspect_bot_admin

    bot = get_bot()
    channels = db.scalars(select(Channel).where(Channel.deleted_at.is_(None))).all()
    for ch in channels:
        result = await inspect_bot_admin(bot, ch.telegram_chat_id)
        ch.bot_is_admin = result.is_admin
        ch.last_checked_at = datetime.now(UTC)
        ch.last_check_error = result.error
        if not result.is_admin:
            log.warning("channel_bot_not_admin", chat_id=ch.telegram_chat_id)


@celery_app.task(name="app.workers.tasks.purge_old_credentials")
def purge_old_credentials():
    from app.core.config import get_settings

    db = SyncSessionLocal()
    try:
        days = get_settings().room_credentials_retention_days
        cutoff = datetime.now(UTC) - timedelta(days=days)
        rows = db.scalars(select(RoomCredential).where(RoomCredential.sent_at.is_not(None), RoomCredential.purged_at.is_(None))).all()
        for c in rows:
            if c.sent_at and c.sent_at < cutoff:
                c.room_id_encrypted = "purged"
                c.room_password_encrypted = "purged"
                c.purged_at = datetime.now(UTC)
        db.commit()
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.send_telegram_message")
def send_telegram_message(telegram_id: int, text: str):
    from app.bot.loader import get_bot

    async def _s():
        await get_bot().send_message(telegram_id, text)

    _run(_s())
