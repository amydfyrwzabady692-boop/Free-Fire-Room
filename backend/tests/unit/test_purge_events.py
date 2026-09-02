from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.announcement import CustomAnnouncement
from app.models.analytics import EventView
from app.models.event import Event, RoomCredential
from app.models.registration import Registration
from app.workers.tasks import _purge_announcements_older_than, _purge_events_older_than
from tests.conftest import make_event, make_organizer, make_user


def test_purge_old_events_removes_past_customs(db):
    host = make_user(db, 801)
    org = make_organizer(db, host)
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=24)

    old = make_event(db, org, title="Old custom")
    old.starts_at = now - timedelta(hours=25)
    old.registration_ends_at = old.starts_at - timedelta(hours=1)
    old.credentials_send_at = old.starts_at - timedelta(minutes=30)

    recent = make_event(db, org, title="Recent custom")
    recent.starts_at = now - timedelta(hours=2)
    recent.registration_ends_at = recent.starts_at - timedelta(hours=1)
    recent.credentials_send_at = recent.starts_at - timedelta(minutes=30)

    future = make_event(db, org, title="Future custom")
    db.commit()

    removed = _purge_events_older_than(db, cutoff)
    db.commit()

    assert removed == 1
    ids = set(db.scalars(select(Event.id)).all())
    assert old.id not in ids
    assert recent.id in ids
    assert future.id in ids


def test_purge_old_events_removes_old_announcements(db):
    user = make_user(db, 802)
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=24)

    old = CustomAnnouncement(
        user_id=user.id,
        title="Old ann",
        channel_name="Ch",
        starts_at=now - timedelta(hours=30),
    )
    recent = CustomAnnouncement(
        user_id=user.id,
        title="Recent ann",
        channel_name="Ch",
        starts_at=now + timedelta(hours=2),
    )
    db.add_all([old, recent])
    db.commit()

    removed = _purge_announcements_older_than(db, cutoff)
    db.commit()

    assert removed == 1
    titles = set(db.scalars(select(CustomAnnouncement.title)).all())
    assert "Old ann" not in titles
    assert "Recent ann" in titles


def test_purge_old_events_removes_registrations_and_credentials(db):
    """A finished custom always has rows hanging off it; those must cascade.

    Deleting through the ORM would try to NULL the NOT NULL event_id on
    registrations/room_credentials instead of cascading, so this used to blow
    up with an IntegrityError on every real custom.
    """
    host = make_user(db, 803)
    org = make_organizer(db, host)
    player = make_user(db, 804)
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=24)

    old = make_event(db, org, title="Old custom with players")
    old.starts_at = now - timedelta(hours=25)
    old.registration_ends_at = old.starts_at - timedelta(hours=1)
    old.credentials_send_at = old.starts_at - timedelta(minutes=30)
    db.add(Registration(event_id=old.id, user_id=player.id, status="confirmed"))
    db.add(
        RoomCredential(
            event_id=old.id,
            room_id_encrypted="x",
            room_password_encrypted="y",
        )
    )
    db.add(EventView(event_id=old.id, user_id=player.id, source="deep_link"))
    db.commit()

    removed = _purge_events_older_than(db, cutoff)
    db.commit()

    assert removed == 1
    assert db.scalars(select(Event.id)).all() == []
    assert db.scalars(select(Registration.id)).all() == []
    assert db.scalars(select(RoomCredential.id)).all() == []
    assert db.scalars(select(EventView.id)).all() == []
