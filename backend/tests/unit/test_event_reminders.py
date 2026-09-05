"""Telling players a prize custom is coming, 1 hour and 10 minutes ahead.

Before this, a reminder only reached players who had already registered - which
is nobody, for a custom nobody has heard of yet.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.enums import EventStatus, EventVisibility, JobStatus, JobType, RegistrationStatus
from app.models.jobs import ScheduledJob
from app.models.registration import Registration
from app.services.scheduler import schedule_event_jobs_sync
from app.workers import tasks
from tests.conftest import make_event, make_organizer, make_user


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.sent.append((chat_id, text))


def _reminder_job(db, event, offset="rem60"):
    job = ScheduledJob(
        job_type=JobType.REMINDER,
        entity_type="event",
        entity_id=event.id,
        run_at=datetime.now(UTC),
        status=JobStatus.RUNNING,
        idempotency_key=f"reminder:{event.id}:{offset}",
        payload={"offset": offset},
    )
    db.add(job)
    db.flush()
    return job


def _live_event(db, org, *, minutes_ahead=60):
    event = make_event(db, org, capacity=0, prize_summary="۱۰۰۰ الماس")
    event.starts_at = datetime.now(UTC) + timedelta(minutes=minutes_ahead)
    event.status = EventStatus.PUBLISHED
    event.visibility = EventVisibility.PUBLIC
    event.deep_link_active = True
    db.flush()
    return event


def test_lead_label_reads_naturally():
    assert tasks._lead_label(60) == "1 ساعت"
    assert tasks._lead_label(75) == "1 ساعت و 15 دقیقه"
    assert tasks._lead_label(10) == "10 دقیقه"
    # never "0 دقیقه" - the job can fire a few seconds late
    assert tasks._lead_label(0) == "1 دقیقه"


def test_offsets_are_one_hour_and_ten_minutes(db):
    host = make_user(db, 5001)
    org = make_organizer(db, host)
    event = _live_event(db, org, minutes_ahead=180)
    schedule_event_jobs_sync(db, event)
    from sqlalchemy import select

    rows = db.scalars(
        select(ScheduledJob).where(ScheduledJob.job_type == JobType.REMINDER)
    ).all()
    offsets = {(j.payload or {}).get("offset") for j in rows}
    assert offsets == {"rem60", "rem10"}


@pytest.mark.asyncio
async def test_reminder_reaches_registered_and_unregistered_users(db, monkeypatch):
    host = make_user(db, 5010)
    org = make_organizer(db, host)
    event = _live_event(db, org)
    joined = make_user(db, 5011)
    stranger = make_user(db, 5012)
    db.add(
        Registration(
            event_id=event.id, user_id=joined.id, status=RegistrationStatus.CONFIRMED
        )
    )
    db.flush()
    job = _reminder_job(db, event)

    monkeypatch.setattr(tasks, "_remind_organizer_before_start", _noop)
    monkeypatch.setattr(tasks, "get_outbound_rate", lambda: 10_000)
    _stub_redis(monkeypatch, taken=False)

    bot = FakeBot()
    await tasks._send_reminders(bot, db, job)

    by_chat = {chat: text for chat, text in bot.sent}
    assert joined.telegram_id in by_chat
    assert "ثبت‌نام کرده‌اید" in by_chat[joined.telegram_id]
    # the person who has never seen this custom is told what the prize is
    assert stranger.telegram_id in by_chat
    assert "۱۰۰۰ الماس" in by_chat[stranger.telegram_id]
    assert "1 ساعت" in by_chat[stranger.telegram_id]
    # the organizer is not spammed as a "stranger" twice
    assert len(bot.sent) == len(set(chat for chat, _ in bot.sent))


@pytest.mark.asyncio
async def test_broadcast_is_skipped_once_another_worker_took_the_lock(db, monkeypatch):
    host = make_user(db, 5020)
    org = make_organizer(db, host)
    event = _live_event(db, org)
    make_user(db, 5021)
    job = _reminder_job(db, event)

    monkeypatch.setattr(tasks, "_remind_organizer_before_start", _noop)
    monkeypatch.setattr(tasks, "get_outbound_rate", lambda: 10_000)
    _stub_redis(monkeypatch, taken=True)

    bot = FakeBot()
    await tasks._send_reminders(bot, db, job)
    assert bot.sent == []


@pytest.mark.asyncio
async def test_broadcast_can_be_turned_off(db, monkeypatch):
    host = make_user(db, 5030)
    org = make_organizer(db, host)
    event = _live_event(db, org)
    stranger = make_user(db, 5031)
    job = _reminder_job(db, event)

    monkeypatch.setattr(tasks, "_remind_organizer_before_start", _noop)
    monkeypatch.setattr(tasks, "get_outbound_rate", lambda: 10_000)
    _stub_redis(monkeypatch, taken=False)
    _stub_setting(monkeypatch, event_reminder_broadcast=False)

    bot = FakeBot()
    await tasks._send_reminders(bot, db, job)
    assert all(chat != stranger.telegram_id for chat, _ in bot.sent)


@pytest.mark.asyncio
async def test_an_archived_custom_sends_no_reminder(db, monkeypatch):
    host = make_user(db, 5040)
    org = make_organizer(db, host)
    event = _live_event(db, org)
    event.archived_at = datetime.now(UTC)
    db.flush()
    make_user(db, 5041)
    job = _reminder_job(db, event)

    monkeypatch.setattr(tasks, "_remind_organizer_before_start", _noop)
    _stub_redis(monkeypatch, taken=False)

    bot = FakeBot()
    await tasks._send_reminders(bot, db, job)
    assert bot.sent == []


# --- helpers ---------------------------------------------------------------


async def _noop(*a, **kw):
    return None


def _stub_redis(monkeypatch, *, taken: bool):
    class _R:
        def set(self, *a, **kw):
            return not taken

    import redis

    monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, *a, **kw: _R()))


def _stub_setting(monkeypatch, **overrides):
    from app.core.config import get_settings

    real = get_settings()

    class _S:
        def __getattr__(self, name):
            if name in overrides:
                return overrides[name]
            return getattr(real, name)

    monkeypatch.setattr("app.core.config.get_settings", lambda: _S())
