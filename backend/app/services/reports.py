from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.helpers import esc
from app.core.config import get_settings
from app.core.enums import EventStatus, ReportReason, ReportStatus
from app.core.time import format_local
from app.models.admin import Admin
from app.models.event import Event, RoomCredential
from app.models.organizer import Organizer
from app.models.report import Report
from app.models.user import User

REPORT_LABELS = {
    ReportReason.NO_CREDENTIALS: "ROOM ID / PASS را سر ساعت نفرستاد",
    ReportReason.UNPAID_PRIZE: "بعد از کاستوم جایزه را نداد",
    ReportReason.WRONG_ROOM: "ROOM ID یا PASS اشتباه بود",
    ReportReason.FAKE_PRIZE: "جایزه دروغ / کاستوم جعلی",
    ReportReason.FAKE_ORGANIZER: "برگزارکننده جعلی",
    ReportReason.SUDDEN_RULE_CHANGE: "قوانین را ناگهان عوض کرد",
    ReportReason.CHEATER: "چیتر در کاستوم",
    ReportReason.INAPPROPRIATE: "محتوای نامناسب",
    ReportReason.OTHER: "مورد دیگر",
}

PLAYER_REASONS = {
    ReportReason.NO_CREDENTIALS,
    ReportReason.UNPAID_PRIZE,
    ReportReason.WRONG_ROOM,
    ReportReason.FAKE_PRIZE,
    ReportReason.CHEATER,
    ReportReason.OTHER,
}
CHEATER_REPORT_LIMIT = 5


def report_label(reason: str) -> str:
    try:
        return REPORT_LABELS.get(ReportReason(reason), reason)
    except ValueError:
        return reason


def format_person(user: User | None) -> str:
    if not user:
        return "نامشخص"
    name = " ".join(part for part in (user.first_name, user.last_name) if part) or "بدون نام"
    uname = f" @{user.username}" if user.username else ""
    return f"{esc(name)}{esc(uname)} — {user.telegram_id}"


def auto_archive_deadline(event: Event) -> datetime:
    """When the bot gives up waiting for the organizer to tap "custom started"."""
    return event.starts_at + timedelta(minutes=get_settings().auto_archive_minutes)


def is_archived(event: Event, now: datetime | None = None) -> bool:
    """Past means the organizer said so - or the backstop expired."""
    if getattr(event, "archived_at", None) is not None:
        return True
    if event.status in {EventStatus.CANCELLED, EventStatus.REJECTED, EventStatus.FINISHED}:
        return True
    now = now or datetime.now(UTC)
    return now > auto_archive_deadline(event)


def credentials_deadline(event: Event) -> datetime:
    """The organizer can still enter ROOM ID / PASS until this moment."""
    archived = getattr(event, "archived_at", None)
    return archived or auto_archive_deadline(event)


def fill_deadline(event: Event) -> datetime:
    """Players can still complete the conditions until this moment."""
    return credentials_deadline(event)


def credentials_window_open(event: Event, now: datetime | None = None) -> bool:
    if event.status in {EventStatus.CANCELLED, EventStatus.REJECTED}:
        return False
    return not is_archived(event, now)


def join_window_open(event: Event, now: datetime | None = None) -> bool:
    if event.status in {
        EventStatus.CANCELLED,
        EventStatus.REJECTED,
        EventStatus.DRAFT,
        EventStatus.FINISHED,
    }:
        return False
    return not is_archived(event, now)


def creds_were_provided(creds: RoomCredential | None) -> bool:
    return bool(creds and not creds.purged_at and creds.room_id_encrypted)


def creds_were_sent(creds: RoomCredential | None) -> bool:
    return bool(creds_were_provided(creds) and creds and creds.sent_at)


async def event_missed_credentials(db: AsyncSession, event: Event) -> bool:
    if credentials_window_open(event):
        return False
    creds = await db.scalar(select(RoomCredential).where(RoomCredential.event_id == event.id))
    return not creds_were_provided(creds)


async def notify_active_admins(bot, db: AsyncSession, text: str) -> set[int]:
    sent: set[int] = set()
    rows = (
        await db.scalars(
            select(Admin).where(Admin.is_active.is_(True)).options(selectinload(Admin.user))
        )
    ).all()
    for admin in rows:
        user = admin.user
        if not user:
            continue
        try:
            await bot.send_message(user.telegram_id, text)
            sent.add(user.telegram_id)
        except Exception:
            continue
    return sent


async def notify_telegram_user(bot, telegram_id: int | None, text: str) -> bool:
    if not telegram_id:
        return False
    try:
        await bot.send_message(telegram_id, text)
        return True
    except Exception:
        return False


def normalize_cheater_name(text: str) -> str:
    raw = (text or "").strip()
    for prefix in ("نام چیتر:", "چیتر:", "cheater:"):
        if raw.lower().startswith(prefix.lower()):
            raw = raw[len(prefix) :].strip()
            break
    return raw[:48]


def format_cheater_body(name: str) -> str:
    return f"نام چیتر: {normalize_cheater_name(name)}"


async def existing_user_report(db: AsyncSession, user_id, event_id) -> Report | None:
    return await db.scalar(
        select(Report)
        .where(
            Report.reporter_id == user_id,
            Report.event_id == event_id,
            Report.reason != ReportReason.CHEATER.value,
        )
        .limit(1)
    )


async def cheater_reports_for_user(db: AsyncSession, user_id, event_id) -> list[Report]:
    rows = (
        await db.scalars(
            select(Report).where(
                Report.reporter_id == user_id,
                Report.event_id == event_id,
                Report.reason == ReportReason.CHEATER.value,
            )
        )
    ).all()
    return list(rows)


async def create_player_report(
    db: AsyncSession,
    *,
    reporter: User,
    event: Event,
    reason: str,
    body: str | None = None,
) -> tuple[Report | None, str | None]:
    try:
        reason_enum = ReportReason(reason)
    except ValueError:
        return None, "دلیل گزارش نامعتبر است."
    if reason_enum not in PLAYER_REASONS:
        return None, "این نوع گزارش از ربات قابل ثبت نیست."

    org = event.organizer or await db.get(Organizer, event.organizer_id)
    if reason_enum != ReportReason.CHEATER and org and org.user_id == reporter.id:
        return None, "نمی‌توانید کاستوم خودتان را گزارش کنید."

    now = datetime.now(UTC)
    if reason_enum == ReportReason.CHEATER:
        name = normalize_cheater_name(body or "")
        if len(name) < 2:
            return None, "نام چیتر را بفرستید (نام داخل Free Fire)."
        if now < event.starts_at:
            return None, "کاستوم هنوز شروع نشده؛ بعد از شروع می‌توانید چیتر را گزارش کنید."
        prev_cheaters = await cheater_reports_for_user(db, reporter.id, event.id)
        if len(prev_cheaters) >= CHEATER_REPORT_LIMIT:
            return None, "برای این کاستوم سقف گزارش چیتر پر شده است."
        seen = {normalize_cheater_name(row.body).casefold() for row in prev_cheaters}
        if name.casefold() in seen:
            return None, "همین نام را قبلاً برای این کاستوم گزارش کرده‌اید."
        body = format_cheater_body(name)
    elif await existing_user_report(db, reporter.id, event.id):
        return None, "قبلاً برای این کاستوم یک گزارش ثبت کرده‌اید."

    if reason_enum == ReportReason.NO_CREDENTIALS:
        if credentials_window_open(event, now):
            return None, "هنوز مهلت ۵ دقیقه‌ای برگزارکننده برای ارسال ROOM ID و PASS تمام نشده است."
        if not await event_missed_credentials(db, event):
            return None, "ROOM ID و PASS در ربات ثبت شده. اگر جایزه نگرفتید یا PASS اشتباه بود، همان گزینه را بزنید."
    if reason_enum == ReportReason.UNPAID_PRIZE and now < event.starts_at:
        return None, "کاستوم هنوز شروع نشده؛ بعد از پایان می‌توانید این مورد را گزارش کنید."
    if reason_enum == ReportReason.WRONG_ROOM and credentials_window_open(event, now):
        return None, "هنوز زمان ارسال ROOM ID / PASS نرسیده یا مهلت تمام نشده است."

    text = (body or "").strip() or report_label(reason_enum)
    report = Report(
        reporter_id=reporter.id,
        event_id=event.id,
        organizer_id=event.organizer_id,
        reason=reason_enum.value,
        body=text[:4000],
        status=ReportStatus.NEW,
    )
    db.add(report)
    await db.flush()
    return report, None


def format_report_alert(*, event: Event, reporter: User, reason: str, body: str, organizer_user: User | None) -> str:
    title = "گزارش چیتر در کاستوم" if reason == ReportReason.CHEATER else "گزارش جدید از بازیکن"
    extra = ""
    if reason == ReportReason.CHEATER:
        extra = f"نام چیتر: {esc(normalize_cheater_name(body))}\n"
    return (
        f"{title}\n\n"
        f"کاستوم: {esc(event.title)}\n"
        f"ساعت: {format_local(event.starts_at, event.timezone)}\n"
        f"دلیل: {report_label(reason)}\n"
        f"{extra}"
        f"گزارش‌دهنده: {format_person(reporter)}\n"
        f"برگزارکننده: {format_person(organizer_user)}\n\n"
        f"{esc(body[:500])}"
    )


def format_cheater_alert_for_organizer(*, event: Event, reporter: User, body: str) -> str:
    return (
        "گزارش چیتر در کاستوم شما\n\n"
        f"کاستوم: {esc(event.title)}\n"
        f"ساعت: {format_local(event.starts_at, event.timezone)}\n"
        f"نام چیتر: {esc(normalize_cheater_name(body))}\n"
        f"گزارش‌دهنده: {format_person(reporter)}"
    )


def format_prize_vote_alert(
    *,
    event: Event,
    reporter: User,
    organizer_user: User | None,
    paid: bool,
    extra: str | None = None,
) -> str:
    verdict = "جایزه را داد" if paid else "جایزه را نداد"
    note = f"\n\n{esc(extra[:400])}" if extra else ""
    return (
        "گزارش جایزه — فقط برای مالک ربات (عمومی نیست)\n\n"
        f"نتیجه: {verdict}\n"
        f"کاستوم: {esc(event.title)}\n"
        f"ساعت: {format_local(event.starts_at, event.timezone)}\n"
        f"گزارش‌دهنده: {format_person(reporter)}\n"
        f"برگزارکننده: {format_person(organizer_user)}"
        f"{note}"
    )
