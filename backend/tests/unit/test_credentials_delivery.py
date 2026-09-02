"""Delivery must never punish a player for our own failures.

The bot used to share one aiogram Bot across event loops, so every membership
check inside a background job raised RuntimeError. get_membership swallowed it
and reported "not a member", which marked every eligible player INELIGIBLE and
sent nothing - while the organizer heard nothing at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.enums import DeliveryStatus, JobStatus, JobType, RegistrationStatus
from app.core.security import encrypt_secret
from app.models.channel import Channel
from app.models.event import EventRequiredChannel, RoomCredential
from app.models.jobs import Delivery, ScheduledJob
from app.models.registration import Registration
from app.services.credentials import deliver_one
from tests.conftest import make_event, make_organizer, make_user


class StubMember:
    def __init__(self, status="member"):
        self.status = status
        self.is_member = True


class StubBot:
    """Minimal stand-in for aiogram's Bot."""

    def __init__(self, *, member_status="member", member_error=None):
        self.member_status = member_status
        self.member_error = member_error
        self.sent: list[tuple[int, str]] = []

    async def get_chat_member(self, chat_id, user_id):
        if self.member_error is not None:
            raise self.member_error
        return StubMember(self.member_status)

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))

        class _Msg:
            message_id = len(self.sent)

        return _Msg()


def _setup(db, telegram_id=555):
    host = make_user(db, telegram_id + 1000)
    org = make_organizer(db, host)
    player = make_user(db, telegram_id)
    event = make_event(db, org, prize_summary="۱۰۰۰ الماس")
    channel = Channel(telegram_chat_id=-1001234, title="کانال", bot_is_admin=True)
    db.add(channel)
    db.flush()
    db.add(EventRequiredChannel(event_id=event.id, channel_id=channel.id, is_active=True))
    reg = Registration(
        event_id=event.id,
        user_id=player.id,
        status=RegistrationStatus.CONFIRMED,
        confirmed_at=datetime.now(UTC),
    )
    db.add(reg)
    event.confirmed_count = 1
    creds = RoomCredential(
        event_id=event.id,
        room_id_encrypted=encrypt_secret("12345678"),
        room_password_encrypted=encrypt_secret("pass123"),
    )
    db.add(creds)
    job = ScheduledJob(
        job_type=JobType.SEND_CREDENTIALS,
        entity_type="event",
        entity_id=event.id,
        run_at=datetime.now(UTC) - timedelta(minutes=1),
        status=JobStatus.RUNNING,
        idempotency_key=f"send_credentials:{event.id}:creds",
    )
    db.add(job)
    db.flush()
    return event, player, reg, creds, job


@pytest.mark.asyncio
async def test_member_receives_credentials(db):
    event, player, reg, creds, job = _setup(db, 601)
    bot = StubBot(member_status="member")

    result = await deliver_one(bot, db, event, player, creds, job)

    assert result == "sent"
    assert len(bot.sent) == 1
    assert "12345678" in bot.sent[0][1]
    assert reg.status == RegistrationStatus.CONFIRMED
    row = db.scalar(select(Delivery).where(Delivery.user_id == player.id))
    assert row.status == DeliveryStatus.SENT


@pytest.mark.asyncio
async def test_infrastructure_error_never_marks_a_player_ineligible(db):
    event, player, reg, creds, job = _setup(db, 602)
    # exactly what the shared-Bot bug raised inside every background job
    bot = StubBot(member_error=RuntimeError("Timeout context manager should be used inside a task"))

    result = await deliver_one(bot, db, event, player, creds, job)

    assert result == "check_unavailable"
    assert reg.status == RegistrationStatus.CONFIRMED, "eligible player was demoted by our own error"
    assert event.confirmed_count == 1
    assert db.scalar(select(Delivery).where(Delivery.user_id == player.id)) is None
    assert bot.sent == []


@pytest.mark.asyncio
async def test_player_who_really_left_the_channel_is_skipped(db):
    event, player, reg, creds, job = _setup(db, 603)
    bot = StubBot(member_status="left")

    result = await deliver_one(bot, db, event, player, creds, job)

    assert result == "skipped"
    assert reg.status == RegistrationStatus.INELIGIBLE
    assert event.confirmed_count == 0
    assert bot.sent == []


@pytest.mark.asyncio
async def test_delivery_is_idempotent(db):
    event, player, reg, creds, job = _setup(db, 604)
    bot = StubBot(member_status="member")

    assert await deliver_one(bot, db, event, player, creds, job) == "sent"
    assert await deliver_one(bot, db, event, player, creds, job) == "already"
    assert len(bot.sent) == 1
