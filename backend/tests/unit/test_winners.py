from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.core.enums import EventStatus
from app.services.reports import fill_deadline, is_archived, join_window_open
from app.services.winners import (
    INELIGIBLE_MSG,
    contact_link,
    normalize_payout_contact,
    winner_eligible,
)
from tests.conftest import make_event, make_organizer, make_user


def test_winner_needs_confirmed_and_received_creds():
    assert winner_eligible(confirmed=True, received_creds=True) is True
    assert winner_eligible(confirmed=False, received_creds=True) is False
    assert winner_eligible(confirmed=True, received_creds=False) is False
    assert winner_eligible(confirmed=False, received_creds=False) is False
    assert "جایزه تعلق نمی‌گیرد" in INELIGIBLE_MSG


def test_join_window_covers_fill_and_send_minutes(db):
    host = make_user(db, 701)
    org = make_organizer(db, host)
    event = make_event(db, org)
    event.starts_at = datetime.now(UTC) - timedelta(minutes=10)
    event.status = EventStatus.STARTED
    db.flush()
    assert join_window_open(event) is True
    assert fill_deadline(event) > datetime.now(UTC)


def test_join_window_stays_open_while_the_organizer_has_not_started(db):
    """The clock no longer closes a custom - the organizer's button does."""
    host = make_user(db, 702)
    org = make_organizer(db, host)
    event = make_event(db, org)
    event.starts_at = datetime.now(UTC) - timedelta(minutes=20)
    event.status = EventStatus.STARTED
    db.flush()
    assert join_window_open(event) is True
    assert is_archived(event) is False


def test_join_window_closes_when_the_organizer_marks_it_started(db):
    host = make_user(db, 704)
    org = make_organizer(db, host)
    event = make_event(db, org)
    event.starts_at = datetime.now(UTC) - timedelta(minutes=5)
    event.status = EventStatus.STARTED
    event.archived_at = datetime.now(UTC)
    db.flush()
    assert is_archived(event) is True
    assert join_window_open(event) is False


def test_join_window_closes_at_the_backstop(db):
    """An organizer who never taps the button still cannot leave it open forever."""
    host = make_user(db, 705)
    org = make_organizer(db, host)
    event = make_event(db, org)
    minutes = get_settings().auto_archive_minutes
    event.starts_at = datetime.now(UTC) - timedelta(minutes=minutes + 5)
    event.status = EventStatus.STARTED
    db.flush()
    assert is_archived(event) is True
    assert join_window_open(event) is False


def test_join_window_closed_when_finished(db):
    host = make_user(db, 703)
    org = make_organizer(db, host)
    event = make_event(db, org)
    event.starts_at = datetime.now(UTC) - timedelta(minutes=2)
    event.status = EventStatus.FINISHED
    db.flush()
    assert join_window_open(event) is False


def test_payout_contact_is_normalised_to_one_handle():
    assert normalize_payout_contact("@my_id") == "@my_id"
    assert normalize_payout_contact("my_id") == "@my_id"
    assert normalize_payout_contact("https://t.me/my_id") == "@my_id"
    assert normalize_payout_contact("  t.me/my_id/  ") == "@my_id"
    assert contact_link("@my_id") == "https://t.me/my_id"


def test_payout_contact_rejects_junk():
    from app.core.errors import AppError

    for bad in ("", "   ", "@", "two words"):
        try:
            normalize_payout_contact(bad)
        except AppError:
            continue
        raise AssertionError(f"accepted {bad!r}")
