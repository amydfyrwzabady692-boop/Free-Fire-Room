from __future__ import annotations

import asyncio
import html
import os
import socket
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select, update

from app.core.enums import BroadcastStatus, DeliveryStatus, EventStatus, JobStatus, JobType
from app.core.logging import get_logger
from app.core.session import SyncSessionLocal
from app.models.broadcast import BroadcastCampaign, BroadcastDelivery
from app.models.channel import Channel
from app.models.event import Event, RoomCredential
from app.models.announcement import CustomAnnouncement
from app.models.jobs import Delivery, Notification, ScheduledJob
from app.models.organizer import Organizer, OrganizerTrustEvent
from app.models.registration import Registration
from app.models.user import User
from app.services import trust
from app.services.credentials import deliver_one
from app.services.scheduler import claim_due_jobs_sync
from app.workers.celery_app import celery_app

log = get_logger("worker")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def _run_with_bot(make_coro):
    """Run a Telegram-sending job in a fresh loop with its own Bot.

    ``asyncio.run`` creates a new event loop each call, and aiogram pins its
    aiohttp session to the loop that first used it. Reusing the process-global
    Bot here raises "Timeout context manager should be used inside a task", so
    every job gets a short-lived Bot that is closed when it finishes.
    """
    from app.bot.loader import make_bot

    async def _main():
        bot = make_bot()
        try:
            return await make_coro(bot)
        finally:
            await bot.session.close()

    return asyncio.run(_main())


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
        _run_with_bot(lambda bot: _send_credentials(bot, db, job))
        return
    if job.job_type == JobType.REMINDER:
        _run_with_bot(lambda bot: _send_reminders(bot, db, job))
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


async def _send_credentials(bot, db, job: ScheduledJob) -> None:
    from redis import Redis
    from app.core.config import get_settings

    event = db.get(Event, job.entity_id)
    if not event or event.status in {EventStatus.CANCELLED, EventStatus.REJECTED, EventStatus.DRAFT}:
        job.status = JobStatus.CANCELLED
        return
    creds = db.scalar(select(RoomCredential).where(RoomCredential.event_id == event.id))
    from app.services.reports import credentials_deadline, creds_were_provided

    now = datetime.now(UTC)
    if not creds_were_provided(creds):
        if now > credentials_deadline(event):
            await _expire_missing_credentials(bot, db, event, job)
            return
        payload = dict(job.payload or {})
        last = payload.get("last_prompt_ts")
        should_prompt = True
        if last:
            try:
                prev = datetime.fromisoformat(last)
                should_prompt = now - prev >= timedelta(minutes=2)
            except ValueError:
                should_prompt = True
        if should_prompt:
            await _prompt_organizer_for_creds(bot, db, event)
            payload["last_prompt_ts"] = now.isoformat()
            payload["prompted"] = True
            job.payload = payload
        job.status = JobStatus.PENDING
        job.run_at = now + timedelta(minutes=1)
        job.last_error = "waiting_organizer_credentials"
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
        sent = failed = skipped = not_admin = unverified = 0
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
            elif result == "check_failed":
                not_admin += 1
            elif result == "check_unavailable":
                unverified += 1
            elif result == "skipped":
                skipped += 1
            else:
                failed += 1
                await asyncio.sleep(min(8, 0.05 * (failed + 1)))
        if not_admin or unverified:
            # nobody was demoted; retry shortly so these players still get their creds
            job.status = JobStatus.PENDING
            job.run_at = datetime.now(UTC) + timedelta(minutes=2)
            job.last_error = "bot_not_admin_on_required_channel" if not_admin else "membership_check_unavailable"
            if not_admin:
                org = db.get(Organizer, event.organizer_id)
                org_user = db.get(User, org.user_id) if org else None
                if org_user:
                    try:
                        await bot.send_message(
                            org_user.telegram_id,
                            f"⚠️ ROOM ID / PASS کاستوم «{html.escape(event.title)}» برای {not_admin} نفر ارسال نشد "
                            "چون ربات دیگر ادمین کانال جوین اجباری نیست.\n"
                            "ربات را دوباره ادمین کنید؛ ربات خودش تا چند دقیقه دیگر دوباره تلاش می‌کند.",
                        )
                    except Exception:
                        log.exception("organizer_bot_not_admin_warn_failed", event_id=str(event.id))
            log.warning(
                "credentials_delivery_incomplete",
                event_id=str(event.id),
                not_admin=not_admin,
                unverified=unverified,
                sent=sent,
            )
            if sent == 0:
                return
        creds.sent_at = datetime.now(UTC)
        event.status = EventStatus.STARTED
        from app.services.reports import fill_deadline

        now = datetime.now(UTC)
        payload = dict(job.payload or {})
        if not payload.get("organizer_notified"):
            # the organizer scheduled these creds ahead of time and has heard
            # nothing since; tell them the send actually happened
            payload["organizer_notified"] = True
            job.payload = payload
            org = db.get(Organizer, event.organizer_id)
            if org and sent:
                trust.record_sync(db, org, "credentials_delivered", related_event_id=event.id)
            await _notify_organizer_delivery_done(bot, db, event, sent=sent, skipped=skipped, failed=failed)
        if now < fill_deadline(event):
            job.status = JobStatus.PENDING
            job.run_at = now + timedelta(minutes=2)
            job.attempts = 0
            job.max_attempts = 40
            job.last_error = None
            job.completed_at = None
        else:
            job.status = JobStatus.DONE
            job.completed_at = now
        log.info("credentials_sent", event_id=str(event.id), sent=sent, failed=failed, skipped=skipped)
    finally:
        redis.delete(lock_key)


async def _notify_organizer_delivery_done(bot, db, event: Event, *, sent: int, skipped: int, failed: int) -> None:
    org = db.get(Organizer, event.organizer_id)
    org_user = db.get(User, org.user_id) if org else None
    if not org_user:
        return
    from app.core.config import get_settings

    fill = get_settings().custom_fill_minutes
    lines = [
        f"✅ ROOM ID / PASS کاستوم «{html.escape(event.title)}» ارسال شد.",
        "",
        f"📨 دریافت کردند: <b>{sent}</b>",
    ]
    if skipped:
        lines.append(f"⏭ ارسال نشد (از کانال خارج شده یا قبلاً گرفته بود): {skipped}")
    if failed:
        lines.append(f"❌ خطا در ارسال: {failed}")
    lines.append("")
    lines.append(
        f"تا {fill} دقیقه بعد از ساعت شروع، هر کس شرایط را کامل کند مشخصات برایش می‌رود "
        "و همین‌جا خبر نهایی را می‌گیرید."
    )
    try:
        await bot.send_message(org_user.telegram_id, "\n".join(lines))
    except Exception:
        log.exception("organizer_delivery_summary_failed", event_id=str(event.id))


async def _remind_organizer_before_start(bot, db, event: Event) -> None:
    """Warn the organizer ahead of time, while they can still act.

    Without this the first thing they hear is the prompt at the very start of
    the 5-minute window, which is easy to miss.
    """
    creds = db.scalar(select(RoomCredential).where(RoomCredential.event_id == event.id))
    from app.services.reports import creds_were_provided

    org = db.get(Organizer, event.organizer_id)
    org_user = db.get(User, org.user_id) if org else None
    if not org_user or org_user.is_bot_blocked:
        return

    from app.core.config import get_settings
    from app.core.time import format_local

    grace = get_settings().credentials_grace_minutes
    minutes = max(0, int((event.starts_at - datetime.now(UTC)).total_seconds() // 60))
    when = format_local(event.starts_at, event.timezone)
    if creds_were_provided(creds):
        text = (
            f"🔔 کاستوم «{html.escape(event.title)}» تا {minutes} دقیقه دیگر ({when}) شروع می‌شود.\n"
            "ROOM ID و PASS را از قبل ثبت کرده‌اید ✅\n"
            "سر ساعت خودکار برای واجدین شرایط ارسال می‌شود و نتیجه را همین‌جا می‌گیرید."
        )
        markup = None
    else:
        text = (
            f"🔔 کاستوم «{html.escape(event.title)}» تا {minutes} دقیقه دیگر ({when}) شروع می‌شود.\n\n"
            "❗️ هنوز ROOM ID و PASS را ثبت نکرده‌اید.\n"
            f"می‌توانید همین حالا ثبت کنید تا سر ساعت خودکار ارسال شود، وگرنه سر ساعت فقط {grace} دقیقه فرصت دارید.\n"
            "دکمه سبز زیر را بزنید."
        )
        from app.bot.keyboards.common import send_creds_kb

        markup = send_creds_kb(event.public_token)
    try:
        await bot.send_message(org_user.telegram_id, text, reply_markup=markup)
    except Exception:
        log.exception("organizer_pre_start_reminder_failed", event_id=str(event.id))


async def _send_reminders(bot, db, job: ScheduledJob) -> None:
    event = db.get(Event, job.entity_id)
    if not event:
        return
    if event.status in {EventStatus.CANCELLED, EventStatus.REJECTED, EventStatus.FINISHED}:
        return
    await _remind_organizer_before_start(bot, db, event)
    regs = db.scalars(select(Registration).where(Registration.event_id == event.id, Registration.status == "confirmed")).all()
    text = (
        f"🔔 یادآوری: کاستوم «{html.escape(event.title)}» به‌زودی شروع می‌شود.\n"
        "اگر کانال‌های جوین اجباری را عضو شده باشید، سر ساعت ROOM ID و PASS برایتان می‌آید."
    )
    for reg in regs:
        user = db.get(User, reg.user_id)
        if not user or user.is_bot_blocked:
            continue
        try:
            await bot.send_message(user.telegram_id, text)
        except Exception:
            continue
        await asyncio.sleep(0.04)


async def _prompt_organizer_for_creds(bot, db, event: Event) -> None:
    org = db.get(Organizer, event.organizer_id)
    if not org:
        return
    user = db.get(User, org.user_id)
    if not user:
        return
    from app.bot.keyboards.common import send_creds_kb
    from app.core.config import get_settings
    from app.core.time import format_local

    grace = get_settings().credentials_grace_minutes
    try:
        await bot.send_message(
            user.telegram_id,
            f"🎮 ساعت کاستوم «{html.escape(event.title)}» رسید ({format_local(event.starts_at, event.timezone)}).\n\n"
            "اول فقط <b>ROOM ID</b> را بفرستید.\n"
            "بعد ربات از شما <b>PASS</b> را جدا می‌پرسد.\n\n"
            f"⏳ فقط {grace} دقیقه فرصت دارید. اگر نفرستید اخطار می‌گیرید و بازیکن‌ها می‌توانند گزارش بدهند.\n"
            f"بعد از ارسال، {get_settings().custom_fill_minutes} دقیقه برای پر شدن کاستوم فرصت هست؛ "
            "هر کس در این مدت شرایط را کامل کند ROOM ID و PASS برایش ارسال می‌شود.\n"
            "دکمه سبز را بزنید و اول ROOM ID را بفرستید.",
            reply_markup=send_creds_kb(event.public_token),
        )
    except Exception:
        log.exception("organizer_cred_prompt_failed", event_id=str(event.id))


async def _expire_missing_credentials(bot, db, event: Event, job: ScheduledJob) -> None:
    from app.bot.keyboards.common import report_reasons_kb
    from app.core.config import get_settings
    from app.models.admin import Admin
    from app.services.reports import format_person

    grace = get_settings().credentials_grace_minutes
    job.status = JobStatus.FAILED
    job.last_error = "organizer_did_not_send_credentials"
    job.completed_at = datetime.now(UTC)
    event.status = EventStatus.FINISHED
    event.finished_at = datetime.now(UTC)

    org = db.get(Organizer, event.organizer_id)
    if org:
        trust.record_sync(db, org, "credentials_missed", related_event_id=event.id)
    org_user = db.get(User, org.user_id) if org else None
    if org_user:
        try:
            await bot.send_message(
                org_user.telegram_id,
                f"⚠️ اخطار: مهلت {grace} دقیقه‌ای ارسال ROOM ID و PASS کاستوم «{html.escape(event.title)}» تمام شد.\n"
                "مشخصات برای بازیکن‌ها ارسال نشد و ممکن است گزارش تخلف دریافت کنید.",
            )
        except Exception:
            log.exception("organizer_missed_creds_warn_failed", event_id=str(event.id))

    regs = db.scalars(
        select(Registration).where(Registration.event_id == event.id, Registration.status == "confirmed")
    ).all()
    player_text = (
        f"برگزارکننده کاستوم «{html.escape(event.title)}» در مهلت {grace} دقیقه‌ای ROOM ID و PASS را نفرستاد.\n"
        "اگر ثبت‌نام کرده بودید، از دکمه زیر به مالک ربات گزارش بدهید."
    )
    for reg in regs:
        user = db.get(User, reg.user_id)
        if not user or user.is_bot_blocked:
            continue
        try:
            await bot.send_message(
                user.telegram_id,
                player_text,
                reply_markup=report_reasons_kb(event.public_token),
            )
        except Exception:
            continue
        await asyncio.sleep(0.04)

    admin_text = (
        f"مهلت ارسال ROOM ID / PASS تمام شد ({grace} دقیقه).\n\n"
        f"کاستوم: {html.escape(event.title)}\n"
        f"برگزارکننده: {format_person(org_user)}\n"
        f"ثبت‌نام قطعی: {event.confirmed_count}"
    )
    admins = db.scalars(select(Admin).where(Admin.is_active.is_(True))).all()
    for admin in admins:
        user = db.get(User, admin.user_id)
        if not user:
            continue
        try:
            await bot.send_message(user.telegram_id, admin_text)
        except Exception:
            continue

    log.info("credentials_window_expired", event_id=str(event.id), confirmed=event.confirmed_count)


@celery_app.task(name="app.workers.tasks.send_event_credentials")
def send_event_credentials(event_id: str):
    db = SyncSessionLocal()
    try:
        from uuid import UUID as _UUID

        event = db.get(Event, _UUID(event_id))
        if not event:
            return
        job = db.scalar(
            select(ScheduledJob)
            .where(ScheduledJob.entity_id == event.id, ScheduledJob.job_type == JobType.SEND_CREDENTIALS)
            .order_by(ScheduledJob.created_at.desc())
        )
        if job is None:
            job = ScheduledJob(
                job_type=JobType.SEND_CREDENTIALS,
                entity_type="event",
                entity_id=event.id,
                run_at=datetime.now(UTC),
                status=JobStatus.RUNNING,
                idempotency_key=f"send_credentials:{event.id}:manual",
            )
            db.add(job)
            db.flush()
        else:
            job.status = JobStatus.RUNNING
        _run_with_bot(lambda bot: _send_credentials(bot, db, job))
        db.commit()
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.run_broadcast", bind=True)
def run_broadcast(self, campaign_id: str):
    db = SyncSessionLocal()
    try:
        _run_with_bot(lambda bot: _broadcast(bot, db, UUID(campaign_id)))
        db.commit()
    finally:
        db.close()


async def _broadcast(bot, db, campaign_id: UUID) -> None:
    camp = db.get(BroadcastCampaign, campaign_id)
    if not camp or camp.status not in {BroadcastStatus.RUNNING, BroadcastStatus.SCHEDULED, "running", "scheduled"}:
        return
    camp.status = BroadcastStatus.RUNNING
    camp.started_at = datetime.now(UTC)
    users = db.scalars(select(User).where(User.deleted_at.is_(None), User.is_bot_blocked.is_(False))).all()
    from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

    for i, user in enumerate(users, start=1):
        if camp.status == BroadcastStatus.PAUSED:
            break
        try:
            await bot.send_message(user.telegram_id, camp.body)
            db.add(BroadcastDelivery(campaign_id=camp.id, user_id=user.id, status=DeliveryStatus.SENT, sent_at=datetime.now(UTC)))
            camp.sent_count += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 0.5)
            try:
                await bot.send_message(user.telegram_id, camp.body)
                db.add(BroadcastDelivery(campaign_id=camp.id, user_id=user.id, status=DeliveryStatus.SENT, sent_at=datetime.now(UTC)))
                camp.sent_count += 1
            except Exception as retry_exc:  # noqa: BLE001
                db.add(BroadcastDelivery(campaign_id=camp.id, user_id=user.id, status=DeliveryStatus.FAILED, error_message=str(retry_exc)))
                camp.fail_count += 1
        except TelegramForbiddenError as exc:
            # user blocked the bot: stop retrying them on every future campaign
            user.is_bot_blocked = True
            db.add(BroadcastDelivery(campaign_id=camp.id, user_id=user.id, status=DeliveryStatus.PERMANENT_FAIL, error_message=str(exc)))
            camp.fail_count += 1
        except Exception as exc:  # noqa: BLE001
            db.add(BroadcastDelivery(campaign_id=camp.id, user_id=user.id, status=DeliveryStatus.FAILED, error_message=str(exc)))
            camp.fail_count += 1
        if i % 50 == 0:
            # checkpoint so a crash mid-campaign does not replay every send
            db.commit()
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
        _run_with_bot(lambda bot: _recheck(bot, db))
        db.commit()
    finally:
        db.close()


async def _recheck(bot, db) -> None:
    from app.services.telegram_ops import inspect_bot_admin

    channels = db.scalars(select(Channel).where(Channel.deleted_at.is_(None))).all()
    for ch in channels:
        result = await inspect_bot_admin(bot, ch.telegram_chat_id)
        ch.bot_is_admin = result.is_admin
        ch.last_checked_at = datetime.now(UTC)
        ch.last_check_error = result.error
        if not result.is_admin:
            log.warning("channel_bot_not_admin", chat_id=ch.telegram_chat_id)


PURGE_BATCH = 200


def _purge_events_older_than(db, cutoff: datetime) -> int:
    """Hard-delete finished customs and everything hanging off them.

    Deletion goes through a Core statement rather than ``db.delete(event)``:
    the ORM would try to NULL out ``registrations.event_id`` /
    ``room_credentials.event_id`` (both NOT NULL) instead of letting the
    database cascade, which fails with an IntegrityError. Tables whose FK is
    not ON DELETE CASCADE are cleared by hand first.
    """
    removed = 0
    while True:
        event_ids = list(
            db.scalars(select(Event.id).where(Event.starts_at < cutoff).limit(PURGE_BATCH))
        )
        if not event_ids:
            return removed

        db.execute(
            update(OrganizerTrustEvent)
            .where(OrganizerTrustEvent.related_event_id.in_(event_ids))
            .values(related_event_id=None)
        )
        db.execute(delete(Notification).where(Notification.event_id.in_(event_ids)))
        db.execute(update(Delivery).where(Delivery.event_id.in_(event_ids)).values(event_id=None))

        job_ids = list(db.scalars(select(ScheduledJob.id).where(ScheduledJob.entity_id.in_(event_ids))))
        if job_ids:
            db.execute(update(Delivery).where(Delivery.job_id.in_(job_ids)).values(job_id=None))
            db.execute(delete(ScheduledJob).where(ScheduledJob.id.in_(job_ids)))

        db.execute(delete(Event).where(Event.id.in_(event_ids)))
        removed += len(event_ids)


def _purge_announcements_older_than(db, cutoff: datetime) -> int:
    rows = list(db.scalars(select(CustomAnnouncement).where(CustomAnnouncement.starts_at < cutoff)))
    for row in rows:
        db.delete(row)
    return len(rows)


@celery_app.task(name="app.workers.tasks.purge_old_events")
def purge_old_events():
    from app.core.config import get_settings

    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(hours=settings.event_retention_hours)
    db = SyncSessionLocal()
    try:
        removed_events = _purge_events_older_than(db, cutoff)
        removed_announcements = _purge_announcements_older_than(db, cutoff)
        db.commit()
        if removed_events or removed_announcements:
            log.info(
                "purge_old_events_done",
                events=removed_events,
                announcements=removed_announcements,
                cutoff=cutoff.isoformat(),
            )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


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
    _run_with_bot(lambda bot: bot.send_message(telegram_id, text))


@celery_app.task(name="app.workers.tasks.send_daily_custom_digest")
def send_daily_custom_digest():
    db = SyncSessionLocal()
    try:
        _run_with_bot(lambda bot: _send_daily_custom_digest(bot, db))
        db.commit()
    finally:
        db.close()


async def _send_daily_custom_digest(bot, db) -> None:
    from redis import Redis
    from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
    from app.bot.keyboards.common import event_list_kb
    from app.core.config import get_settings
    from app.core.enums import UserStatus
    from app.core.time import DEFAULT_TZ, to_tz
    from app.services.digest import digest_button_items, format_daily_digest, upcoming_prize_customs_sync

    settings = get_settings()
    day_key = to_tz(datetime.now(UTC), DEFAULT_TZ).date().isoformat()
    redis = Redis.from_url(settings.redis_url)
    lock_key = f"lock:daily_digest:{day_key}"
    try:
        locked = bool(redis.set(lock_key, WORKER_ID, nx=True, ex=26 * 3600))
    except Exception:
        log.exception("daily_digest_lock_failed")
        locked = True
    if not locked:
        log.info("daily_digest_already_sent", day=day_key)
        return

    events = upcoming_prize_customs_sync(db)
    if not events:
        log.info("daily_digest_skipped_empty")
        return

    text = format_daily_digest(events)
    markup = event_list_kb(digest_button_items(events), mode="digest")
    users = db.scalars(
        select(User).where(
            User.deleted_at.is_(None),
            User.is_bot_blocked.is_(False),
            User.notification_enabled.is_(True),
            User.status == UserStatus.ACTIVE,
        )
    ).all()
    sent = failed = skipped = 0
    for user in users:
        if not user.telegram_id:
            skipped += 1
            continue
        try:
            await bot.send_message(user.telegram_id, text, reply_markup=markup)
            sent += 1
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 0.5)
            try:
                await bot.send_message(user.telegram_id, text, reply_markup=markup)
                sent += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            user.is_bot_blocked = True
            skipped += 1
        except Exception:
            failed += 1
        await asyncio.sleep(1 / max(get_outbound_rate(), 1))
        if (sent + failed + skipped) % 50 == 0:
            db.commit()
    log.info("daily_digest_sent", day=day_key, users=len(users), sent=sent, failed=failed, skipped=skipped)
