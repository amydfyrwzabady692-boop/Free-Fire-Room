"""The organizer owns the clock, and nothing caps how many players join."""

from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.core.enums import EventStatus, RegistrationStatus
from app.models.organizer import Organizer
from app.models.user import User, UserProfile
from app.services.event_display import format_capacity_line, format_event_list_label
from app.services.events import capacity_is_unlimited, mark_event_started
from app.services.registration import register_user
from app.services.reports import credentials_window_open, is_archived, join_window_open
from tests.conftest import make_event, make_organizer, make_user


async def _seed(async_db, *, capacity=0, minutes_ago=0):
    from app.core.security import generate_unguessable_token
    from app.models.event import Event

    host = User(telegram_id=8100, first_name="host")
    async_db.add(host)
    await async_db.flush()
    async_db.add(UserProfile(user_id=host.id))
    org = Organizer(user_id=host.id, status="approved")
    async_db.add(org)
    await async_db.flush()
    now = datetime.now(UTC)
    event = Event(
        public_token=generate_unguessable_token(12),
        organizer_id=org.id,
        title="Custom",
        starts_at=now - timedelta(minutes=minutes_ago),
        registration_ends_at=now,
        credentials_send_at=now,
        capacity=capacity,
        status=EventStatus.PUBLISHED,
        waitlist_enabled=True,
        timezone="Asia/Tehran",
        region="ME",
        game_mode="squad",
    )
    async_db.add(event)
    await async_db.flush()
    return org, event


async def _players(async_db, count, start=8200):
    out = []
    for i in range(count):
        u = User(telegram_id=start + i, first_name=f"p{i}")
        async_db.add(u)
        await async_db.flush()
        async_db.add(UserProfile(user_id=u.id))
        out.append(u)
    await async_db.flush()
    return out


async def test_unlimited_capacity_never_fills_or_waitlists(async_db):
    """The 100-player cap is gone: everyone who qualifies gets in."""
    _, event = await _seed(async_db, capacity=0)
    assert capacity_is_unlimited(event) is True
    for player in await _players(async_db, 120):
        result = await register_user(
            async_db, user=player, event=event, bot=None, accept_rules=True
        )
        assert result.registration.status == RegistrationStatus.CONFIRMED
        assert result.waitlisted is False
    assert event.confirmed_count == 120
    assert event.status == EventStatus.PUBLISHED


async def test_a_real_capacity_still_fills(async_db):
    """An explicit capacity (only settable through the API) still applies."""
    _, event = await _seed(async_db, capacity=2)
    statuses = []
    for player in await _players(async_db, 3, start=8400):
        result = await register_user(
            async_db, user=player, event=event, bot=None, accept_rules=True
        )
        statuses.append(result.registration.status)
    assert statuses[:2] == [RegistrationStatus.CONFIRMED, RegistrationStatus.CONFIRMED]
    assert statuses[2] == RegistrationStatus.WAITLISTED
    assert event.status == EventStatus.FULL


async def test_start_button_is_what_moves_a_custom_to_the_past(async_db):
    org, event = await _seed(async_db, minutes_ago=20)
    # 20 minutes past its start time and still open, because nobody closed it
    assert is_archived(event) is False
    assert join_window_open(event) is True
    assert credentials_window_open(event) is True

    await mark_event_started(async_db, event, org.user_id)

    assert event.archived_at is not None
    assert event.status == EventStatus.STARTED
    assert is_archived(event) is True
    assert join_window_open(event) is False
    assert credentials_window_open(event) is False


async def test_marking_started_twice_is_a_no_op(async_db):
    org, event = await _seed(async_db)
    await mark_event_started(async_db, event, org.user_id)
    first = event.archived_at
    await mark_event_started(async_db, event, org.user_id)
    assert event.archived_at == first


def test_capacity_line_says_unlimited(db):
    host = make_user(db, 8001)
    org = make_organizer(db, host)
    event = make_event(db, org, capacity=0)
    event.confirmed_count = 37
    line = format_capacity_line(event)
    assert "بدون محدودیت" in line
    assert "37" in line


def test_list_label_shows_running_not_past_until_archived(db):
    host = make_user(db, 8002)
    org = make_organizer(db, host)
    event = make_event(db, org, capacity=0, prize_summary="۱۰۰ الماس")
    event.starts_at = datetime.now(UTC) - timedelta(minutes=10)
    db.flush()
    assert "در حال برگزاری" in format_event_list_label(event)

    event.archived_at = datetime.now(UTC)
    db.flush()
    assert format_event_list_label(event).startswith("گذشته")


def test_list_label_falls_back_to_past_at_the_backstop(db):
    host = make_user(db, 8003)
    org = make_organizer(db, host)
    event = make_event(db, org, capacity=0, prize_summary="۱۰۰ الماس")
    event.starts_at = datetime.now(UTC) - timedelta(minutes=get_settings().auto_archive_minutes + 5)
    db.flush()
    assert format_event_list_label(event).startswith("گذشته")


async def _with_creds(async_db, event):
    from app.core.security import encrypt_secret
    from app.models.event import RoomCredential

    async_db.add(
        RoomCredential(
            event_id=event.id,
            room_id_encrypted=encrypt_secret("12345678"),
            room_password_encrypted=encrypt_secret("pass"),
        )
    )
    await async_db.flush()


async def test_a_newcomer_gets_the_room_right_up_to_the_start_button(async_db, monkeypatch):
    """The whole point: joining late is not a reason to miss the room.

    Someone who hears about the custom only after a friend forwarded them the
    ROOM ID must still get their own copy from the bot, on the spot.
    """
    from app.services.credentials import queue_late_credentials

    queued: list[str] = []
    from app.workers import enqueue

    monkeypatch.setattr(enqueue, "spawn", lambda task, *a: queued.append(a[0]))

    org, event = await _seed(async_db, minutes_ago=25)
    await _with_creds(async_db, event)

    # 25 minutes past the start, still open: the room goes out
    assert await queue_late_credentials(async_db, event) is True
    assert queued == [str(event.id)]

    # the organizer taps "custom started" - and it stops, immediately
    queued.clear()
    await mark_event_started(async_db, event, org.user_id)
    assert await queue_late_credentials(async_db, event) is False
    assert queued == []


async def test_nothing_is_queued_before_the_organizer_gives_the_room(async_db, monkeypatch):
    from app.services.credentials import queue_late_credentials

    queued: list[str] = []
    from app.workers import enqueue

    monkeypatch.setattr(enqueue, "spawn", lambda task, *a: queued.append(a[0]))

    _, event = await _seed(async_db)
    assert await queue_late_credentials(async_db, event) is False
    assert queued == []


async def test_the_backstop_closes_the_room_too(async_db, monkeypatch):
    from app.services.credentials import queue_late_credentials

    monkeypatch.setattr("app.workers.enqueue.spawn", lambda task, *a: None)
    _, event = await _seed(async_db, minutes_ago=get_settings().auto_archive_minutes + 5)
    await _with_creds(async_db, event)
    assert await queue_late_credentials(async_db, event) is False
