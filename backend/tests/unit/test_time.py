from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core.time import combine_local_date_and_clock, local_day_bounds, parse_clock, upcoming_local_dates


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("22", (22, 0)),
        ("22:00", (22, 0)),
        ("۲۲:۳۰", (22, 30)),
        ("9", (9, 0)),
        ("0930", (9, 30)),
        ("ساعت 21", (21, 0)),
    ],
)
def test_parse_clock(text, expected):
    assert parse_clock(text) == expected


@pytest.mark.parametrize("text", ["", "25", "22:99", "abc"])
def test_parse_clock_rejects_bad_values(text):
    with pytest.raises(ValueError):
        parse_clock(text)


def test_upcoming_local_dates_has_today_tomorrow_and_day_after():
    items = upcoming_local_dates(3)
    assert [item["offset"] for item in items] == [0, 1, 2]
    assert items[0]["label"].startswith("امروز")
    assert items[1]["label"].startswith("فردا")
    assert items[2]["label"].startswith("پس‌فردا")
    assert items[0]["date"] <= items[1]["date"] <= items[2]["date"]


def test_combine_local_date_and_clock_uses_tehran():
    from zoneinfo import ZoneInfo

    when = combine_local_date_and_clock(date(2026, 8, 15), 22, 0)
    local = when.astimezone(ZoneInfo("Asia/Tehran"))
    assert local.year == 2026
    assert local.month == 8
    assert local.day == 15
    assert local.hour == 22
    assert local.minute == 0


def test_local_day_bounds_use_tehran():
    start, end = local_day_bounds(now=datetime(2026, 8, 15, 22, 0, tzinfo=ZoneInfo("Asia/Tehran")))
    assert end - start == timedelta(days=1)
    assert start.astimezone(ZoneInfo("Asia/Tehran")).hour == 0
    assert start.tzinfo is not None
