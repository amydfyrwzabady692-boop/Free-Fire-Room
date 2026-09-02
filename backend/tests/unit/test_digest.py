from datetime import UTC, datetime, timedelta

from app.core.enums import EventStatus
from app.services.digest import digest_button_items, format_daily_digest, upcoming_prize_customs_sync
from tests.conftest import make_event, make_organizer, make_user


def test_upcoming_prize_customs_only_lists_open_ones(db):
    """Open means the organizer has not closed it - not "in the future"."""
    host = make_user(db, 801)
    org = make_organizer(db, host)
    live = make_event(db, org, title="کاستوم الماس")
    make_event(db, org, title="تمام‌شده", status=EventStatus.FINISHED)
    running = make_event(db, org, title="در حال برگزاری")
    running.starts_at = datetime.now(UTC) - timedelta(hours=2)
    closed = make_event(db, org, title="بسته‌شده")
    closed.starts_at = datetime.now(UTC) - timedelta(hours=2)
    closed.archived_at = datetime.now(UTC)
    old = make_event(db, org, title="خیلی کهنه")
    old.starts_at = datetime.now(UTC) - timedelta(days=3)
    db.flush()
    rows = upcoming_prize_customs_sync(db)
    titles = [e.title for e in rows]
    assert "کاستوم الماس" in titles
    assert "در حال برگزاری" in titles
    assert "تمام‌شده" not in titles
    assert "بسته‌شده" not in titles
    assert "خیلی کهنه" not in titles
    assert live.public_token in {token for token, _ in digest_button_items(rows)}


def test_daily_digest_text_tells_users_to_join(db):
    host = make_user(db, 802)
    org = make_organizer(db, host)
    make_event(db, org, title="کاستوم الماس", prize_summary="۱۰۰۰ الماس")
    text = format_daily_digest(upcoming_prize_customs_sync(db))
    assert "کاستوم‌های جایزه‌دار پیش‌رو" in text
    assert "عضو شدم" in text
    assert "۱۰۰۰ الماس" in text
    assert "ROOM ID" in text
    assert "PASS" in text
