from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import jdatetime


DEFAULT_TZ = "Asia/Tehran"
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_EN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_tz(dt: datetime, tz_name: str = DEFAULT_TZ) -> datetime:
    return as_utc(dt).astimezone(ZoneInfo(tz_name))


def local_day_bounds(tz_name: str = DEFAULT_TZ, *, now: datetime | None = None) -> tuple[datetime, datetime]:
    local = (now or datetime.now(UTC)).astimezone(ZoneInfo(tz_name))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)


def _jalali(dt: datetime, tz_name: str = DEFAULT_TZ) -> jdatetime.datetime:
    local = to_tz(dt, tz_name).replace(tzinfo=None)
    return jdatetime.datetime.fromgregorian(datetime=local)


def to_fa_digits(text: str) -> str:
    return text.translate(_FA_DIGITS)


def format_local(dt: datetime, tz_name: str = DEFAULT_TZ, jalali: bool = True, *, compact: bool = False) -> str:
    if not jalali:
        return to_tz(dt, tz_name).strftime("%Y-%m-%d %H:%M")
    j = _jalali(dt, tz_name)
    weekdays = getattr(jdatetime.date, "j_weekdays_fa", None) or (
        "شنبه",
        "یکشنبه",
        "دوشنبه",
        "سه‌شنبه",
        "چهارشنبه",
        "پنجشنبه",
        "جمعه",
    )
    months = getattr(jdatetime.date, "j_months_fa", None) or (
        "فروردین",
        "اردیبهشت",
        "خرداد",
        "تیر",
        "مرداد",
        "شهریور",
        "مهر",
        "آبان",
        "آذر",
        "دی",
        "بهمن",
        "اسفند",
    )
    weekday = weekdays[j.weekday()]
    month_list = list(months)
    month = month_list[j.month] if len(month_list) == 13 else month_list[j.month - 1]
    clock = f"{j.hour:02d}:{j.minute:02d}"
    if compact:
        text = f"{j.day} {month} {j.year} {clock}"
    else:
        text = f"{weekday} {j.day} {month} {j.year} — {clock}"
    return to_fa_digits(text)


def example_jalali_input(tz_name: str = DEFAULT_TZ) -> str:
    j = _jalali(datetime.now(UTC), tz_name)
    return f"{j.year:04d}/{j.month:02d}/{j.day:02d} {j.hour:02d}:{j.minute:02d}"


def _month_name(j) -> str:
    months = getattr(jdatetime.date, "j_months_fa", None) or (
        "فروردین",
        "اردیبهشت",
        "خرداد",
        "تیر",
        "مرداد",
        "شهریور",
        "مهر",
        "آبان",
        "آذر",
        "دی",
        "بهمن",
        "اسفند",
    )
    month_list = list(months)
    return month_list[j.month] if len(month_list) == 13 else month_list[j.month - 1]


def format_jalali_date(value: date, tz_name: str = DEFAULT_TZ) -> str:
    j = jdatetime.date.fromgregorian(date=value)
    weekdays = getattr(jdatetime.date, "j_weekdays_fa", None) or (
        "شنبه",
        "یکشنبه",
        "دوشنبه",
        "سه‌شنبه",
        "چهارشنبه",
        "پنجشنبه",
        "جمعه",
    )
    text = f"{weekdays[j.weekday()]} {j.day} {_month_name(j)}"
    return to_fa_digits(text)


def upcoming_local_dates(days: int = 3, tz_name: str = DEFAULT_TZ) -> list[dict]:
    now = datetime.now(ZoneInfo(tz_name))
    names = ("امروز", "فردا", "پس‌فردا")
    items = []
    for offset in range(days):
        local = now + timedelta(days=offset)
        day = local.date()
        title = names[offset] if offset < len(names) else f"{offset} روز بعد"
        items.append(
            {
                "offset": offset,
                "date": day,
                "label": f"{title} — {format_jalali_date(day, tz_name)}",
            }
        )
    return items


def parse_clock(text: str) -> tuple[int, int]:
    raw = (text or "").strip().translate(_EN_DIGITS)
    raw = raw.replace("ساعت", " ").replace(".", ":").replace("،", ":")
    raw = re.sub(r"\s+", "", raw)
    hour = minute = None
    if re.fullmatch(r"\d{1,2}", raw):
        hour, minute = int(raw), 0
    elif re.fullmatch(r"\d{1,2}:\d{2}", raw):
        hour, minute = (int(p) for p in raw.split(":"))
    elif re.fullmatch(r"\d{3,4}", raw):
        padded = raw.zfill(4)
        hour, minute = int(padded[:2]), int(padded[2:])
    if hour is None or not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("bad clock")
    return hour, minute


def combine_local_date_and_clock(day: date, hour: int, minute: int, tz_name: str = DEFAULT_TZ) -> datetime:
    naive = datetime(day.year, day.month, day.day, hour, minute)
    return parse_naive_in_tz(naive, tz_name)


def parse_user_datetime(text: str, tz_name: str = DEFAULT_TZ) -> datetime:
    """Parse a user-typed datetime. Jalali is the default (1400s). Gregorian still accepted."""
    raw = (text or "").strip().translate(_EN_DIGITS)
    raw = raw.replace("ساعت", " ").replace("،", " ").replace(",", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    raw = raw.replace("/", "-").replace(".", "-")
    naive = None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d-%H:%M", "%Y-%m-%d %H", "%Y-%m-%d"):
        try:
            naive = datetime.strptime(raw, fmt)
            if fmt == "%Y-%m-%d":
                naive = naive.replace(hour=0, minute=0)
            break
        except ValueError:
            continue
    if naive is None:
        raise ValueError("bad date")
    if naive.year < 1700:
        j = jdatetime.datetime(naive.year, naive.month, naive.day, naive.hour, naive.minute)
        g = j.togregorian()
        naive = datetime(g.year, g.month, g.day, g.hour, g.minute)
    return parse_naive_in_tz(naive, tz_name)


def parse_naive_in_tz(value: datetime, tz_name: str) -> datetime:
    """Interpret a naive datetime as local wall time in tz, return UTC."""
    if value.tzinfo is not None:
        return as_utc(value)
    return value.replace(tzinfo=ZoneInfo(tz_name)).astimezone(UTC)
