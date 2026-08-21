from datetime import UTC, datetime, timedelta

from app.core.enums import EventStatus
from app.services.reports import fill_deadline, join_window_open
from app.services.winners import INELIGIBLE_MSG, winner_eligible
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


def test_join_window_closes_after_fill_deadline(db):
    host = make_user(db, 702)
    org = make_organizer(db, host)
    event = make_event(db, org)
    event.starts_at = datetime.now(UTC) - timedelta(minutes=40)
    event.status = EventStatus.STARTED
    db.flush()
    assert join_window_open(event) is False


def test_join_window_closed_when_finished(db):
    host = make_user(db, 703)
    org = make_organizer(db, host)
    event = make_event(db, org)
    event.starts_at = datetime.now(UTC) - timedelta(minutes=2)
    event.status = EventStatus.FINISHED
    db.flush()
    assert join_window_open(event) is False
