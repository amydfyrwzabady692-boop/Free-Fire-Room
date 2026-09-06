"""Repeating a custom, the headcount on a button, and the organizer profile."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.enums import EventStatus, EventVisibility, SocialPlatform
from app.models.event import Event, RoomCredential
from app.models.organizer import Organizer
from app.models.user import User, UserProfile
from app.services.event_display import format_event_list_label
from app.services.events import repeat_event
from app.services.organizers import (
    format_organizer_profile,
    organizer_deep_link,
    organizer_stats,
    upcoming_events_for,
)
from app.services.social import list_tasks, seed_tasks
from tests.conftest import make_event, make_organizer, make_user

# --- the headcount on the list button ------------------------------------


def test_a_busy_custom_shows_its_headcount(db):
    host = make_user(db, 4001)
    org = make_organizer(db, host)
    event = make_event(db, org, capacity=0, prize_summary="۱۰۰۰ الماس")
    event.confirmed_count = 47
    db.flush()
    label = format_event_list_label(event)
    assert "👥 47" in label
    assert "۱۰۰۰ الماس" in label
    assert len(label) <= 64


def test_an_empty_custom_does_not_show_a_zero(db):
    host = make_user(db, 4002)
    org = make_organizer(db, host)
    event = make_event(db, org, capacity=0, prize_summary="۱۰۰۰ الماس")
    event.confirmed_count = 0
    db.flush()
    assert "👥" not in format_event_list_label(event)


def test_the_headcount_never_pushes_the_label_over_the_limit(db):
    host = make_user(db, 4003)
    org = make_organizer(db, host)
    event = make_event(
        db,
        org,
        capacity=0,
        prize_summary="واریز ۵۰۰ هزار تومان به کارت برای نفر اول و ۲۵۰ هزار برای نفر دوم و سوم",
    )
    event.confirmed_count = 9999
    db.flush()
    label = format_event_list_label(event)
    assert len(label) <= 64, label
    assert "👥 9999" in label


# --- repeating a custom ---------------------------------------------------


async def _source(async_db, *, pages=(), with_creds=True):
    from app.core.security import encrypt_secret, generate_unguessable_token
    from app.models.channel import Channel
    from app.models.event import EventPrize, EventRequiredChannel

    host = User(telegram_id=4100, first_name="host", username="hostguy")
    async_db.add(host)
    await async_db.flush()
    async_db.add(UserProfile(user_id=host.id))
    org = Organizer(user_id=host.id, status="approved", display_name="Host")
    async_db.add(org)
    await async_db.flush()
    channel = Channel(telegram_chat_id=-100123, title="Chan", bot_is_admin=True)
    async_db.add(channel)
    await async_db.flush()
    now = datetime.now(UTC)
    event = Event(
        public_token=generate_unguessable_token(12),
        organizer_id=org.id,
        channel_id=channel.id,
        title="شب جمعه",
        description="کاستوم کلن",
        banner_file_id="banner-1",
        starts_at=now - timedelta(hours=1),
        registration_ends_at=now,
        credentials_send_at=now,
        capacity=0,
        status=EventStatus.PUBLISHED,
        waitlist_enabled=True,
        timezone="Asia/Tehran",
        region="ME",
        game_mode="squad",
        prize_summary="۱۰۰۰ الماس",
        payout_contact="@payme",
        social_url=pages[0] if pages else None,
        social_platform=SocialPlatform.INSTAGRAM if pages else None,
        reminder_offsets_minutes=[60, 10],
    )
    async_db.add(event)
    await async_db.flush()
    async_db.add(EventRequiredChannel(event_id=event.id, channel_id=channel.id, is_active=True))
    async_db.add(EventPrize(event_id=event.id, place=1, title="۱۰۰۰ الماس"))
    if pages:
        seed_tasks(async_db, event, [{"url": u, "platform": None} for u in pages])
    if with_creds:
        async_db.add(
            RoomCredential(
                event_id=event.id,
                room_id_encrypted=encrypt_secret("12345678"),
                room_password_encrypted=encrypt_secret("pass"),
                sent_at=now,
            )
        )
    await async_db.flush()
    return org, event, host


async def test_repeat_carries_everything_that_matters(async_db):
    pages = ["https://instagram.com/a", "https://youtube.com/@b"]
    org, source, host = await _source(async_db, pages=pages)
    when = datetime.now(UTC) + timedelta(hours=6)

    copy = await repeat_event(async_db, source, org, host.id, when)

    assert copy.id != source.id
    assert copy.public_token != source.public_token
    assert copy.starts_at == when
    # the things an organizer would hate to retype
    assert copy.prize_summary == source.prize_summary
    assert copy.payout_contact == "@payme"
    assert copy.description == source.description
    assert copy.banner_file_id == "banner-1"
    assert copy.channel_id == source.channel_id
    assert copy.capacity == 0
    assert [t.url for t in await list_tasks(async_db, copy.id)] == pages
    from sqlalchemy import select

    from app.models.event import EventRequiredChannel

    linked = (
        await async_db.scalars(
            select(EventRequiredChannel).where(EventRequiredChannel.event_id == copy.id)
        )
    ).all()
    assert len(linked) == 1
    assert linked[0].channel_id == source.channel_id


async def test_repeat_does_not_carry_the_room(async_db):
    """Reusing the room would hand the previous players the new one for free."""
    from sqlalchemy import select

    org, source, host = await _source(async_db)
    copy = await repeat_event(
        async_db, source, org, host.id, datetime.now(UTC) + timedelta(hours=6)
    )
    creds = await async_db.scalar(
        select(RoomCredential).where(RoomCredential.event_id == copy.id)
    )
    assert creds is None


async def test_repeat_starts_with_a_clean_slate(async_db):
    org, source, host = await _source(async_db)
    source.confirmed_count = 42
    source.archived_at = datetime.now(UTC)
    await async_db.flush()

    copy = await repeat_event(
        async_db, source, org, host.id, datetime.now(UTC) + timedelta(hours=6)
    )
    assert copy.confirmed_count == 0
    assert copy.archived_at is None
    assert copy.status == EventStatus.DRAFT  # until submit_for_publish runs


async def test_repeat_rejects_a_time_in_the_past(async_db):
    from app.core.errors import AppError

    org, source, host = await _source(async_db)
    with pytest.raises(AppError):
        await repeat_event(
            async_db, source, org, host.id, datetime.now(UTC) - timedelta(hours=2)
        )


# --- the public profile ---------------------------------------------------


async def test_profile_counts_what_a_stranger_cares_about(async_db):
    org, first, host = await _source(async_db)
    # a second custom whose room went out, and a cancelled one
    from app.core.security import encrypt_secret, generate_unguessable_token

    now = datetime.now(UTC)
    second = Event(
        public_token=generate_unguessable_token(12),
        organizer_id=org.id,
        title="دومی",
        starts_at=now + timedelta(hours=2),
        registration_ends_at=now,
        credentials_send_at=now,
        capacity=0,
        status=EventStatus.PUBLISHED,
        visibility=EventVisibility.PUBLIC,
        deep_link_active=True,
        waitlist_enabled=True,
        timezone="Asia/Tehran",
        region="ME",
        game_mode="squad",
        prize_summary="۵۰۰ الماس",
        confirmed_count=30,
    )
    cancelled = Event(
        public_token=generate_unguessable_token(12),
        organizer_id=org.id,
        title="لغوشده",
        starts_at=now,
        registration_ends_at=now,
        credentials_send_at=now,
        capacity=0,
        status=EventStatus.CANCELLED,
        waitlist_enabled=True,
        timezone="Asia/Tehran",
        region="ME",
        game_mode="squad",
    )
    async_db.add_all([second, cancelled])
    await async_db.flush()
    async_db.add(
        RoomCredential(
            event_id=second.id,
            room_id_encrypted=encrypt_secret("1"),
            room_password_encrypted=encrypt_secret("2"),
            sent_at=now,
        )
    )
    first.confirmed_count = 12
    await async_db.flush()

    stats = await organizer_stats(async_db, org.id)
    assert stats["held"] == 2, "the cancelled one is not a custom they held"
    assert stats["delivered"] == 2
    assert stats["cancelled"] == 1
    assert stats["players"] == 42


async def test_profile_reads_like_a_card(async_db):
    org, source, host = await _source(async_db)
    org.verified_badge = True
    org.bio = "کلن ما هر شب کاستوم دارد"
    await async_db.flush()

    text = await format_organizer_profile(async_db, org)
    assert "Host" in text
    assert "اعتبار" in text
    assert "کاستوم برگزار کرده" in text
    assert "کلن ما هر شب" in text
    assert len(text) < 4096


async def test_profile_warns_about_a_bad_organizer(async_db):
    org, source, host = await _source(async_db)
    org.trust_score = 12.0
    await async_db.flush()
    text = await format_organizer_profile(async_db, org)
    assert "با احتیاط" in text


async def test_profile_lists_only_open_public_customs(async_db):
    from app.core.security import generate_unguessable_token

    org, open_one, host = await _source(async_db)
    open_one.starts_at = datetime.now(UTC) + timedelta(hours=1)
    open_one.visibility = EventVisibility.PUBLIC
    open_one.deep_link_active = True
    now = datetime.now(UTC)
    closed = Event(
        public_token=generate_unguessable_token(12),
        organizer_id=org.id,
        title="بسته",
        starts_at=now,
        registration_ends_at=now,
        credentials_send_at=now,
        capacity=0,
        status=EventStatus.PUBLISHED,
        visibility=EventVisibility.PUBLIC,
        deep_link_active=True,
        archived_at=now,
        waitlist_enabled=True,
        timezone="Asia/Tehran",
        region="ME",
        game_mode="squad",
    )
    hidden = Event(
        public_token=generate_unguessable_token(12),
        organizer_id=org.id,
        title="پنهان",
        starts_at=now + timedelta(hours=1),
        registration_ends_at=now,
        credentials_send_at=now,
        capacity=0,
        status=EventStatus.PUBLISHED,
        visibility=EventVisibility.UNLISTED,
        deep_link_active=True,
        waitlist_enabled=True,
        timezone="Asia/Tehran",
        region="ME",
        game_mode="squad",
    )
    async_db.add_all([closed, hidden])
    await async_db.flush()

    rows = await upcoming_events_for(async_db, org.id)
    titles = [e.title for e in rows]
    assert titles == ["شب جمعه"], titles


def test_the_profile_link_is_shareable():
    link = organizer_deep_link("11111111-1111-1111-1111-111111111111")
    assert link.startswith("https://t.me/")
    assert "?start=org_11111111-1111-1111-1111-111111111111" in link
    assert len(link.split("?start=")[1]) <= 64, "Telegram caps the start payload at 64"
