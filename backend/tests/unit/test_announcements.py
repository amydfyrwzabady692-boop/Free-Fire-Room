from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import ValidationAppError
from app.models.announcement import CustomAnnouncement
from app.services.events import _validate_times
from tests.conftest import make_user


def test_equal_event_times_allowed():
    when = datetime.now(UTC) + timedelta(hours=2)
    _validate_times(when, when, when)


def test_event_times_still_reject_late_registration():
    start = datetime.now(UTC) + timedelta(hours=2)
    with pytest.raises(ValidationAppError):
        _validate_times(start, start + timedelta(minutes=1), start)


def test_announcement_row(db):
    user = make_user(db, 501)
    row = CustomAnnouncement(
        user_id=user.id,
        title="کاستوم الماس",
        channel_name="FF Room",
        starts_at=datetime.now(UTC) + timedelta(hours=3),
        prize_summary="الماس",
        extra_join_links=None,
        status="published",
    )
    db.add(row)
    db.flush()
    assert row.id is not None
    found = db.get(CustomAnnouncement, row.id)
    assert found.channel_name == "FF Room"
    found.status = "hidden"
    db.flush()
    assert db.get(CustomAnnouncement, row.id).status == "hidden"
