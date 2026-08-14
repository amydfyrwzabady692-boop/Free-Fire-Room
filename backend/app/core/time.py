from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import jdatetime


DEFAULT_TZ = "Asia/Tehran"


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_tz(dt: datetime, tz_name: str = DEFAULT_TZ) -> datetime:
    return as_utc(dt).astimezone(ZoneInfo(tz_name))


def format_local(dt: datetime, tz_name: str = DEFAULT_TZ, jalali: bool = True) -> str:
    local = to_tz(dt, tz_name)
    if jalali:
        j = jdatetime.datetime.fromgregorian(datetime=local.replace(tzinfo=None))
        return j.strftime("%Y/%m/%d %H:%M")
    return local.strftime("%Y-%m-%d %H:%M")


def parse_naive_in_tz(value: datetime, tz_name: str) -> datetime:
    """Interpret a naive datetime as local wall time in tz, return UTC."""
    if value.tzinfo is not None:
        return as_utc(value)
    return value.replace(tzinfo=ZoneInfo(tz_name)).astimezone(UTC)
