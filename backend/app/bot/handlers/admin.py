from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.access import is_active_admin, menu_for
from app.bot.helpers import esc, extract_channel_ref, replace_callback_view
from app.bot.keyboards.common import (
    DANGER,
    PRIMARY,
    SUCCESS,
    add_required_channel_kb,
    ibtn,
    labeled,
    winner_reply_kb,
)
from app.bot.paging import page_header, paged_kb, paginate, parse_page
from app.bot.states.groups import AdminSG
from app.core.config import get_settings
from app.core.enums import (
    BanScope,
    EventStatus,
    OrganizerStatus,
    RegistrationStatus,
    ReportStatus,
    UserStatus,
    WinnerClaimStatus,
)
from app.core.errors import AppError
from app.core.time import utcnow, format_local
from app.models.announcement import CustomAnnouncement
from app.models.broadcast import BroadcastCampaign
from app.models.channel import GlobalRequiredChannel
from app.models.event import Event
from app.models.jobs import Delivery
from app.models.organizer import Organizer
from app.models.registration import Registration
from app.models.report import Report
from app.models.user import Ban, User
from app.models.winner import WinnerClaim
from app.services import channels as channel_svc
from app.services import events as event_svc
from app.services import organizers as org_svc
from app.services import settings as settings_svc
from app.services import trust as trust_svc
from app.services.announcements import hide_announcement
from app.services.audit import write_audit
from app.services.funnel import biggest_drop, event_funnel, format_funnel
from app.services.reports import format_person, report_label
from app.locales.labels import ban_scope_fa, event_status_fa, setting_fa
from app.services.users import get_by_telegram
from app.services.winners import contact_link, format_payout_note, resolve_payout_contact

router = Router(name="admin")

HOME = "adm:home"

HOME_TEXT = (
    "👑 <b>پنل مالک ربات</b>\n"
    "<i>فقط برای صاحب همین ربات. پنل برگزارکننده جداست.</i>\n\n"
    "گزارش تخلف، بن، کانال اجباری ورود، تنظیمات و آمار کل ربات."
)


def _admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn("داشبورد", callback_data="adm:dash", style=PRIMARY)],
            [
                ibtn("کاستوم‌های در انتظار", callback_data="adm:ev:0", style=SUCCESS),
                ibtn("برگزارکنندگان", callback_data="adm:org:0", style=PRIMARY),
            ],
            [
                ibtn("جستجوی کاربر", callback_data="adm:usr", style=PRIMARY),
                ibtn("کانال اجباری", callback_data="adm:ch:0", style=PRIMARY),
            ],
            [
                ibtn("گزارش تخلف", callback_data="adm:rep:0", style=DANGER),
                ibtn("برنده‌ها", callback_data="adm:win:0", style=SUCCESS),
            ],
            [ibtn("ارسال همگانی", callback_data="adm:bc", style=PRIMARY)],
            [
                ibtn("اطلاع‌رسانی‌ها", callback_data="adm:ann:0", style=PRIMARY),
                ibtn("کاربران اخیر", callback_data="adm:lu:0", style=PRIMARY),
            ],
            [ibtn("همه کاستوم‌ها و آمار", callback_data="adm:all:0", style=SUCCESS)],
            [ibtn("تنظیمات ربات", callback_data="adm:cfg", style=PRIMARY)],
            [ibtn("تعمیرات ربات", callback_data="adm:mt", style=DANGER)],
            [ibtn("منوی بازیکن", callback_data="adm:player", style=PRIMARY)],
        ]
    )


def _back_kb(target: str = HOME) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[ibtn("بازگشت به پنل مالک ربات", callback_data=target, style=PRIMARY)]]
    )


async def _deny(event: Message | CallbackQuery) -> None:
    text = "این بخش فقط برای مدیر ربات است."
    if isinstance(event, CallbackQuery):
        await event.answer(text, show_alert=True)
        return
    await event.answer(text)


async def _ok(db: AsyncSession, user: User | None) -> bool:
    return await is_active_admin(db, user)


async def _guard(cb: CallbackQuery, db: AsyncSession, db_user: User) -> bool:
    """Every callback goes through here. True when the tap may proceed."""
    if await _ok(db, db_user):
        return True
    await _deny(cb)
    return False


async def _view(cb: CallbackQuery, text: str, kb: InlineKeyboardMarkup) -> None:
    """One panel message, edited in place.

    Each section used to answer a tap with 10-20 separate messages, which
    Telegram throttles at roughly one per second per chat: the tail arrived late
    or not at all, and the owner had no way back to the top.
    """
    await replace_callback_view(cb, text, inline=kb)


def _uuid_or_none(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except (ValueError, AttributeError):
        return None


@router.callback_query(F.data == "nav:noop")
async def nav_noop(cb: CallbackQuery):
    """The "3/7" counter in a pager is a label, not a dead button."""
    await cb.answer()


# --------------------------------------------------------------------- home


@router.message(Command("admin"))
@router.message(F.text.in_(labeled("پنل ادمین", "پنل مالک ربات")))
async def admin_home(message: Message, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ok(db, db_user):
        await _deny(message)
        return
    await state.clear()
    await message.answer(HOME_TEXT, reply_markup=_admin_kb())


@router.callback_query(F.data == HOME)
async def admin_home_cb(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _guard(cb, db, db_user):
        return
    await state.clear()
    await _view(cb, HOME_TEXT, _admin_kb())


@router.callback_query(F.data == "adm:player")
async def admin_to_player(cb: CallbackQuery, db: AsyncSession, db_user: User):
    await replace_callback_view(cb, "منوی بازیکن", menu=await menu_for(db, db_user))


# ---------------------------------------------------------------- dashboard


@router.callback_query(F.data == "adm:dash")
async def admin_dash(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    now = utcnow()
    day = now - timedelta(days=1)
    week = now - timedelta(days=7)
    live = [EventStatus.PUBLISHED, EventStatus.FULL, EventStatus.STARTED]

    users = await db.scalar(select(func.count()).select_from(User).where(User.deleted_at.is_(None)))
    new_users = await db.scalar(
        select(func.count()).select_from(User).where(User.deleted_at.is_(None), User.created_at >= day)
    )
    active_day = await db.scalar(
        select(func.count()).select_from(User).where(User.deleted_at.is_(None), User.last_seen_at >= day)
    )
    active_week = await db.scalar(
        select(func.count()).select_from(User).where(User.deleted_at.is_(None), User.last_seen_at >= week)
    )
    banned = await db.scalar(select(func.count()).select_from(Ban).where(Ban.is_active.is_(True)))
    orgs = await db.scalar(select(func.count()).select_from(Organizer))
    pending_orgs = await db.scalar(
        select(func.count()).select_from(Organizer).where(Organizer.status == OrganizerStatus.PENDING)
    )
    # every event count filters deleted_at; the dashboard used to include
    # soft-deleted customs and over-report
    pending_events = await db.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.deleted_at.is_(None), Event.status == EventStatus.PENDING_APPROVAL)
    )
    events_active = await db.scalar(
        select(func.count()).select_from(Event).where(Event.deleted_at.is_(None), Event.status.in_(live))
    )
    hosted = await db.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.deleted_at.is_(None), Event.status != EventStatus.DRAFT)
    )
    regs = await db.scalar(
        select(func.count()).select_from(Registration).where(Registration.status == RegistrationStatus.CONFIRMED)
    )
    sent = await db.scalar(select(func.count()).select_from(Delivery).where(Delivery.status == "sent"))
    failed = await db.scalar(
        select(func.count()).select_from(Delivery).where(Delivery.status.in_(["failed", "permanent_fail"]))
    )
    open_reports = await db.scalar(
        select(func.count()).select_from(Report).where(Report.status == ReportStatus.NEW)
    )
    pending_wins = await db.scalar(
        select(func.count()).select_from(WinnerClaim).where(WinnerClaim.status == WinnerClaimStatus.PENDING)
    )
    anns = await db.scalar(
        select(func.count()).select_from(CustomAnnouncement).where(CustomAnnouncement.status == "published")
    )
    maint = await settings_svc.get_setting(db, "maintenance_mode", False)

    todo = []
    if pending_events:
        todo.append(f"• {pending_events} کاستوم منتظر تأیید")
    if pending_orgs:
        todo.append(f"• {pending_orgs} برگزارکننده منتظر تأیید")
    if open_reports:
        todo.append(f"• {open_reports} گزارش تخلف باز")
    if pending_wins:
        todo.append(f"• {pending_wins} ادعای برنده بررسی‌نشده")
    todo_block = ("\n\n<b>⚡️ نیاز به رسیدگی</b>\n" + "\n".join(todo)) if todo else "\n\nهیچ کاری روی زمین نمانده ✅"

    text = (
        "📊 <b>داشبورد مالک ربات</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"👥 کاربران: <b>{users}</b> (۲۴ساعت: +{new_users})\n"
        f"🟢 فعال ۲۴ساعت: {active_day} | ۷روز: {active_week}\n"
        f"🚫 بن فعال: {banned}\n"
        "━━━━━━━━━━━━━━\n"
        f"🎤 برگزارکننده: <b>{orgs}</b> (در انتظار: {pending_orgs})\n"
        f"🎮 کاستوم ثبت‌شده: <b>{hosted}</b>\n"
        f"🔴 فعال الان: {events_active} | منتظر تأیید: {pending_events}\n"
        f"✅ ثبت‌نام قطعی بازیکن‌ها: {regs}\n"
        "━━━━━━━━━━━━━━\n"
        f"📨 ارسال مشخصات موفق: <b>{sent}</b> | ناموفق: {failed}\n"
        f"📢 اطلاع‌رسانی فعال: {anns}\n"
        f"🔧 حالت تعمیرات: {'<b>روشن</b>' if maint else 'خاموش'}\n"
        f"🗑 پاک‌سازی خودکار کاستوم‌ها: بعد از {get_settings().event_retention_hours} ساعت"
        f"{todo_block}"
    )
    await _view(cb, text, _back_kb())


# ------------------------------------------------------- all customs + stats


def _event_label(e: Event, limit: int = 26) -> str:
    raw = (e.prize_summary or e.title or "کاستوم").strip()
    return raw[:limit]


@router.callback_query(F.data.startswith("adm:all"))
async def admin_all_events(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    rows = (
        await db.scalars(
            select(Event)
            .where(Event.deleted_at.is_(None), Event.status != EventStatus.DRAFT)
            .options(selectinload(Event.organizer).selectinload(Organizer.user))
            .order_by(Event.starts_at.desc())
            .limit(60)
        )
    ).all()
    page = paginate(rows, parse_page(cb.data, "adm:all"))
    if not page.total:
        await _view(cb, page_header("🎮 همه کاستوم‌ها", page, empty="هنوز هیچ کاستومی ثبت نشده."), _back_kb())
        return
    lines = [page_header("🎮 همه کاستوم‌ها", page, empty=""), ""]
    for e in page.items:
        org = e.organizer.display_name if e.organizer else "-"
        prize = (e.prize_summary or "").strip() or "—"
        lines.append(
            f"🎁 <b>{esc(prize[:60])}</b>\n"
            f"🕐 {format_local(e.starts_at, e.timezone, compact=True)} · {event_status_fa(e.status)}\n"
            f"👤 {esc(org)}"
        )
        lines.append("")
    kb = paged_kb(
        "adm:all",
        page,
        item_button=lambda e: ibtn(
            f"آمار: {_event_label(e)}", callback_data=f"adm:evd:{e.public_token}", style=PRIMARY
        ),
        back=HOME,
    )
    await _view(cb, "\n".join(lines).strip(), kb)


@router.callback_query(F.data.startswith("adm:evd:"))
async def admin_event_detail(cb: CallbackQuery, db: AsyncSession, db_user: User):
    """Full detail for one custom, including the funnel."""
    if not await _guard(cb, db, db_user):
        return
    from app.services.event_display import event_public_load_options, format_event_identity_block

    token = cb.data.split(":", 2)[-1]
    # event_public_load_options covers everything the identity block reads,
    # including required_channels - a lazy load here raises under AsyncSession
    e = await db.scalar(
        select(Event)
        .where(Event.public_token == token)
        .options(*event_public_load_options())
    )
    if not e:
        await cb.answer("این کاستوم دیگر وجود ندارد (احتمالاً پاک‌سازی خودکار شده).", show_alert=True)
        return
    stats = await event_funnel(db, e.id)
    hint = biggest_drop(stats)
    org_user = e.organizer.user if e.organizer else None
    trust_line = trust_svc.format_trust_line(e.organizer) if e.organizer else ""
    text = (
        f"{format_event_identity_block(e)}\n"
        "━━━━━━━━━━━━━━\n"
        f"🕐 {format_local(e.starts_at, e.timezone)}\n"
        f"وضعیت: {event_status_fa(e.status)}\n"
        f"مسئول: {format_person(org_user)}\n"
        f"{trust_line}\n"
        "━━━━━━━━━━━━━━\n"
        f"{format_funnel(stats)}"
    )
    if hint:
        text += f"\n\n💡 {hint}"
    buttons = []
    if org_user:
        buttons.append(
            [ibtn("پروندهٔ برگزارکننده", callback_data=f"adm:uid:{org_user.telegram_id}", style=PRIMARY)]
        )
    if e.status in {EventStatus.PUBLISHED, EventStatus.FULL, EventStatus.STARTED}:
        buttons.append([ibtn("لغو این کاستوم", callback_data=f"adm:ec:{e.public_token}", style=DANGER)])
    buttons.append([ibtn("بازگشت به فهرست", callback_data="adm:all:0", style=PRIMARY)])
    await _view(cb, text, InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("adm:ec:"))
async def admin_event_cancel(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    token = cb.data.split(":", 2)[-1]
    e = await db.scalar(select(Event).where(Event.public_token == token))
    if not e:
        await cb.answer("یافت نشد", show_alert=True)
        return
    if e.status in {EventStatus.CANCELLED, EventStatus.FINISHED}:
        await cb.answer("این کاستوم قابل لغو نیست.", show_alert=True)
        return
    await event_svc.cancel_event(db, e, db_user.id, "لغو توسط مالک ربات")
    await cb.answer("لغو شد")
    await _view(cb, f"کاستوم «{esc(e.title)}» لغو شد و jobهایش متوقف شدند.", _back_kb("adm:all:0"))


# --------------------------------------------------------- pending approval


async def _notify_organizer(cb: CallbackQuery, db: AsyncSession, event: Event, text: str) -> None:
    """Approving or rejecting used to be silent from the organizer's side."""
    org = await db.get(Organizer, event.organizer_id)
    user = await db.get(User, org.user_id) if org else None
    if not user or user.is_bot_blocked:
        return
    try:
        await cb.bot.send_message(user.telegram_id, text)
    except Exception:  # noqa: BLE001
        pass


@router.callback_query(F.data.startswith("adm:ev:"))
async def admin_events(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    rows = (
        await db.scalars(
            select(Event)
            .where(Event.status == EventStatus.PENDING_APPROVAL, Event.deleted_at.is_(None))
            .options(selectinload(Event.organizer))
            .order_by(Event.created_at.desc())
            .limit(60)
        )
    ).all()
    page = paginate(rows, parse_page(cb.data, "adm:ev"))
    if not page.total:
        await _view(
            cb,
            page_header("⏳ کاستوم‌های در انتظار تأیید", page, empty="هیچ کاستومی منتظر تأیید نیست ✅"),
            _back_kb(),
        )
        return
    lines = [page_header("⏳ کاستوم‌های در انتظار تأیید", page, empty=""), ""]
    rows_kb = []
    for e in page.items:
        org = e.organizer.display_name if e.organizer else "-"
        lines.append(
            f"🎁 <b>{esc(_event_label(e, 60))}</b>\n"
            f"🕐 {format_local(e.starts_at, e.timezone, compact=True)} · 👤 {esc(org)}"
        )
        lines.append("")
        rows_kb.append(
            [
                ibtn(f"تأیید {_event_label(e, 16)}", callback_data=f"adm:ea:{e.id}", style=SUCCESS),
                ibtn("رد", callback_data=f"adm:er:{e.id}", style=DANGER),
            ]
        )
    await _view(cb, "\n".join(lines).strip(), paged_kb("adm:ev", page, extra_rows=rows_kb, back=HOME))


@router.callback_query(F.data.startswith("adm:ea:"))
async def admin_event_approve(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    event_id = _uuid_or_none(cb.data.split(":")[-1])
    event = await db.get(Event, event_id) if event_id else None
    if not event:
        await cb.answer("یافت نشد", show_alert=True)
        return
    try:
        await event_svc.approve_event(db, event, db_user.id)
    except AppError as exc:
        await cb.answer(exc.message, show_alert=True)
        return
    await _notify_organizer(
        cb, db, event, f"✅ کاستوم «{esc(event.title)}» تأیید و منتشر شد.\nلینکش را در کانالتان بگذارید."
    )
    await cb.answer("منتشر شد")
    await admin_events(cb, db, db_user)


@router.callback_query(F.data.startswith("adm:er:"))
async def admin_event_reject(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    event_id = _uuid_or_none(cb.data.split(":")[-1])
    event = await db.get(Event, event_id) if event_id else None
    if not event:
        await cb.answer("یافت نشد", show_alert=True)
        return
    try:
        await event_svc.reject_event(db, event, db_user.id, "رد از پنل ربات")
    except AppError as exc:
        await cb.answer(exc.message, show_alert=True)
        return
    await _notify_organizer(cb, db, event, f"❌ کاستوم «{esc(event.title)}» توسط مدیریت رد شد.")
    await cb.answer("رد شد")
    await admin_events(cb, db, db_user)


# ------------------------------------------------------------- organizers


@router.callback_query(F.data.startswith("adm:org"))
async def admin_orgs(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    rows = list(
        (
            await db.scalars(
                select(Organizer).options(selectinload(Organizer.user)).order_by(Organizer.created_at.desc()).limit(60)
            )
        ).all()
    )
    # people waiting on you come first, then the least trustworthy
    rows.sort(key=lambda o: (o.status != OrganizerStatus.PENDING, o.trust_score or 0.0))
    page = paginate(rows, parse_page(cb.data, "adm:org"))
    if not page.total:
        await _view(cb, page_header("🎤 برگزارکنندگان", page, empty="هنوز برگزارکننده‌ای ثبت نشده."), _back_kb())
        return

    hosted_rows = (
        await db.execute(
            select(Event.organizer_id, func.count())
            .where(Event.deleted_at.is_(None), Event.status != EventStatus.DRAFT)
            .group_by(Event.organizer_id)
        )
    ).all()
    hosted_map = {oid: int(n or 0) for oid, n in hosted_rows}

    lines = [page_header("🎤 برگزارکنندگان", page, empty=""), ""]
    rows_kb = []
    for org in page.items:
        u = org.user
        name = org.display_name or (u.first_name if u else "-")
        flag = "⏳ " if org.status == OrganizerStatus.PENDING else ""
        lines.append(
            f"{flag}<b>{esc(name)}</b> — {u.telegram_id if u else '-'}\n"
            f"{trust_svc.format_trust_line(org, prefix='اعتبار')}\n"
            f"کاستوم ثبت‌کرده: {hosted_map.get(org.id, 0)}"
        )
        lines.append("")
        if org.status == OrganizerStatus.PENDING:
            rows_kb.append(
                [
                    ibtn(f"تأیید {name[:12]}", callback_data=f"adm:oa:{org.id}", style=SUCCESS),
                    ibtn("رد", callback_data=f"adm:oj:{org.id}", style=DANGER),
                ]
            )
        elif u:
            rows_kb.append([ibtn(f"پرونده {name[:18]}", callback_data=f"adm:uid:{u.telegram_id}", style=PRIMARY)])
    await _view(cb, "\n".join(lines).strip(), paged_kb("adm:org", page, extra_rows=rows_kb, back=HOME))


@router.callback_query(F.data.startswith("adm:oa:"))
async def admin_org_approve(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    org_id = _uuid_or_none(cb.data.split(":")[-1])
    org = await db.get(Organizer, org_id) if org_id else None
    if not org:
        await cb.answer("یافت نشد", show_alert=True)
        return
    await org_svc.approve_organizer(db, org, db_user.id, verified=True)
    user = await db.get(User, org.user_id)
    if user and not user.is_bot_blocked:
        try:
            await cb.bot.send_message(
                user.telegram_id,
                "✅ حساب برگزارکنندهٔ شما تأیید شد.\nاز «پنل برگزارکننده» می‌توانید کاستوم بگذارید.",
            )
        except Exception:  # noqa: BLE001
            pass
    await cb.answer("تأیید شد")
    await admin_orgs(cb, db, db_user)


@router.callback_query(F.data.startswith("adm:oj:"))
async def admin_org_reject(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    org_id = _uuid_or_none(cb.data.split(":")[-1])
    org = await db.get(Organizer, org_id) if org_id else None
    if not org:
        await cb.answer("یافت نشد", show_alert=True)
        return
    await org_svc.reject_organizer(db, org, db_user.id, "رد از پنل ربات")
    await cb.answer("رد شد")
    await admin_orgs(cb, db, db_user)


# ------------------------------------------------------------ user dossier


@router.callback_query(F.data == "adm:usr")
async def admin_user_ask(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _guard(cb, db, db_user):
        return
    await state.set_state(AdminSG.user_query)
    await _view(
        cb,
        "🔎 <b>جستجوی کاربر</b>\n\nشناسه عددی تلگرام کاربر را بفرستید.\n"
        "<i>کاربر باید حداقل یک بار ربات را /start کرده باشد. برای خروج «لغو» بزنید.</i>",
        _back_kb(),
    )


@router.message(AdminSG.user_query)
async def admin_user_show(message: Message, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ok(db, db_user):
        await _deny(message)
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("فقط عدد شناسه تلگرام را بفرستید. برای خروج «لغو» بزنید.")
        return
    await state.clear()
    text, kb = await _user_dossier(db, int(raw))
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("adm:uid:"))
async def admin_user_by_id(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    try:
        telegram_id = int(cb.data.split(":")[-1])
    except ValueError:
        await cb.answer("نامعتبر", show_alert=True)
        return
    text, kb = await _user_dossier(db, telegram_id)
    await _view(cb, text, kb)


async def _user_dossier(db: AsyncSession, telegram_id: int) -> tuple[str, InlineKeyboardMarkup]:
    target = await get_by_telegram(db, telegram_id)
    if not target:
        return "کاربر یافت نشد. اول باید ربات را /start کرده باشد.", _back_kb()
    bans = (
        await db.scalars(
            select(Ban).where(Ban.user_id == target.id, Ban.is_active.is_(True)).order_by(Ban.created_at.desc())
        )
    ).all()
    org = await db.scalar(select(Organizer).where(Organizer.user_id == target.id))
    hosted = int(
        await db.scalar(
            select(func.count())
            .select_from(Event)
            .join(Organizer, Organizer.id == Event.organizer_id)
            .where(Organizer.user_id == target.id, Event.deleted_at.is_(None), Event.status != EventStatus.DRAFT)
        )
        or 0
    )
    joined = int(
        await db.scalar(
            select(func.count())
            .select_from(Registration)
            .where(Registration.user_id == target.id, Registration.status == RegistrationStatus.CONFIRMED)
        )
        or 0
    )
    from_link = int(
        await db.scalar(
            select(func.count())
            .select_from(Registration)
            .where(Registration.user_id == target.id, Registration.source == "deep_link")
        )
        or 0
    )
    reports_against = 0
    if org:
        reports_against = int(
            await db.scalar(select(func.count()).select_from(Report).where(Report.organizer_id == org.id)) or 0
        )
    last_events = (
        await db.scalars(
            select(Event)
            .join(Organizer, Organizer.id == Event.organizer_id)
            .where(Organizer.user_id == target.id, Event.deleted_at.is_(None))
            .order_by(Event.starts_at.desc())
            .limit(5)
        )
    ).all()
    return _render_dossier(target, org, bans, last_events, hosted, joined, from_link, reports_against)


def _render_dossier(target, org, bans, last_events, hosted, joined, from_link, reports_against):
    ban_line = "آزاد ✅"
    if bans:
        ban_line = " | ".join(f"{ban_scope_fa(b.scope)}: {esc(b.reason)}" for b in bans)
    extra = ""
    for e in last_events:
        extra += (
            f"\n• {format_local(e.starts_at, e.timezone, compact=True)}"
            f" | {esc(_event_label(e, 40))} | {event_status_fa(e.status)}"
        )
    trust_line = f"\n{trust_svc.format_trust_line(org)}" if org else ""
    report_line = f"⚠️ گزارش علیه او: {reports_against}\n" if org else ""
    username = target.username or "-"
    text = (
        f"👤 <b>{esc(target.first_name or '-')}</b>\n"
        f"یوزرنیم: @{esc(username)}\n"
        f"شناسه: <code>{target.telegram_id}</code>\n"
        f"عضویت: {format_local(target.created_at, compact=True)}"
        f"{trust_line}\n"
        "━━━━━━━━━━━━━━\n"
        f"🎮 کاستوم ثبت‌کرده: {hosted}\n"
        f"✅ ثبت‌نام قطعی به‌عنوان بازیکن: {joined}\n"
        f"🔗 از لینک اختصاصی آمده: {from_link}\n"
        f"{report_line}"
        f"🚫 وضعیت بن: {ban_line}"
    )
    if extra:
        text += f"\n\n<b>آخرین کاستوم‌هایش:</b>{extra}"
    buttons = [
        [
            ibtn("بن کامل ربات", callback_data=f"adm:bn:{target.telegram_id}", style=DANGER),
            ibtn("بن برگزاری", callback_data=f"adm:bno:{target.telegram_id}", style=DANGER),
        ]
    ]
    if bans:
        buttons.append([ibtn("رفع بن", callback_data=f"adm:ub:{target.telegram_id}", style=SUCCESS)])
    if org:
        buttons.append([ibtn("سابقهٔ اعتبار", callback_data=f"adm:tr:{org.id}", style=PRIMARY)])
    buttons.append([ibtn("بازگشت به پنل", callback_data=HOME, style=PRIMARY)])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("adm:tr:"))
async def admin_trust_history(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    org_id = _uuid_or_none(cb.data.split(":")[-1])
    org = await db.get(Organizer, org_id) if org_id else None
    if not org:
        await cb.answer("یافت نشد", show_alert=True)
        return
    rows = await trust_svc.history(db, org.id, limit=12)
    lines = [
        f"<b>سابقهٔ اعتبار — {esc(org.display_name or '-')}</b>",
        trust_svc.format_trust_line(org, prefix="امتیاز فعلی"),
        "",
    ]
    if not rows:
        lines.append("هنوز رویدادی ثبت نشده؛ امتیاز روی مقدار اولیه است.")
    for row in rows:
        sign = "+" if row.delta >= 0 else ""
        lines.append(
            f"{format_local(row.created_at, compact=True)} · <b>{sign}{row.delta:g}</b>\n{esc(row.reason)}"
        )
    user = await db.get(User, org.user_id)
    back = f"adm:uid:{user.telegram_id}" if user else HOME
    await _view(cb, "\n".join(lines), _back_kb(back))


# ------------------------------------------------------------------- bans


@router.callback_query(F.data.startswith("adm:bn:") | F.data.startswith("adm:bno:"))
async def admin_ban_ask(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _guard(cb, db, db_user):
        return
    parts = cb.data.split(":")
    scope = BanScope.BOT if parts[1] == "bn" else BanScope.ORGANIZE
    try:
        target_tg = int(parts[-1])
    except ValueError:
        await cb.answer("نامعتبر", show_alert=True)
        return
    await state.set_state(AdminSG.ban_reason)
    await state.update_data(target_tg=target_tg, ban_scope=str(scope))
    label = "کل ربات" if scope == BanScope.BOT else "برگزاری / اطلاع‌رسانی"
    await _view(
        cb,
        f"🚫 <b>بن {label}</b>\n\nدلیل را بنویسید (حداقل ۳ حرف).\n"
        "<i>همین متن برای خود کاربر فرستاده می‌شود. برای خروج «لغو» بزنید.</i>",
        _back_kb(),
    )


@router.message(AdminSG.ban_reason)
async def admin_ban_do(message: Message, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ok(db, db_user):
        await _deny(message)
        return
    reason = (message.text or "").strip()
    if len(reason) < 3:
        await message.answer("دلیل خیلی کوتاه است. یک جمله بنویسید یا «لغو» بزنید.")
        return
    data = await state.get_data()
    target = await get_by_telegram(db, int(data["target_tg"]))
    if not target:
        await state.clear()
        await message.answer("کاربر یافت نشد.")
        return
    scope = BanScope(data.get("ban_scope") or BanScope.BOT)

    # update the open ban instead of stacking another row for the same scope
    existing = await db.scalar(
        select(Ban).where(Ban.user_id == target.id, Ban.scope == scope, Ban.is_active.is_(True))
    )
    if existing:
        existing.reason = reason
        existing.created_by = db_user.id
        action = "user_ban_updated"
    else:
        db.add(Ban(user_id=target.id, scope=scope, reason=reason, is_active=True, created_by=db_user.id))
        action = "user_banned"
    if scope == BanScope.BOT:
        target.status = UserStatus.BANNED
    await write_audit(
        db,
        action=action,
        entity_type="user",
        entity_id=target.id,
        actor_id=db_user.id,
        extra={"reason": reason, "scope": str(scope)},
    )
    await state.clear()
    if not target.is_bot_blocked:
        try:
            await message.bot.send_message(
                target.telegram_id, f"دسترسی شما محدود شد ({ban_scope_fa(scope)}).\nدلیل: {esc(reason)}"
            )
        except Exception:  # noqa: BLE001
            pass
    tail = " — بن قبلی به‌روزرسانی شد." if existing else "."
    await message.answer(
        f"کاربر <code>{target.telegram_id}</code> بن شد ({ban_scope_fa(scope)}){tail}",
        reply_markup=_back_kb(),
    )


@router.callback_query(F.data.startswith("adm:ub:"))
async def admin_unban(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    try:
        telegram_id = int(cb.data.split(":")[-1])
    except ValueError:
        await cb.answer("نامعتبر", show_alert=True)
        return
    target = await get_by_telegram(db, telegram_id)
    if not target:
        await cb.answer("یافت نشد", show_alert=True)
        return
    rows = (await db.scalars(select(Ban).where(Ban.user_id == target.id, Ban.is_active.is_(True)))).all()
    for row in rows:
        row.is_active = False
    target.status = UserStatus.ACTIVE
    await write_audit(db, action="user_unbanned", entity_type="user", entity_id=target.id, actor_id=db_user.id)
    if not target.is_bot_blocked:
        try:
            await cb.bot.send_message(target.telegram_id, "✅ محدودیت حساب شما برداشته شد.")
        except Exception:  # noqa: BLE001
            pass
    await cb.answer("بن برداشته شد")
    text, kb = await _user_dossier(db, telegram_id)
    await _view(cb, text, kb)


# ------------------------------------------------------- required channels


@router.callback_query(F.data.startswith("adm:ch"))
async def admin_channels(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    rows = (
        await db.scalars(
            select(GlobalRequiredChannel)
            .options(selectinload(GlobalRequiredChannel.channel))
            .order_by(GlobalRequiredChannel.sort_order)
        )
    ).all()
    page = paginate(rows, parse_page(cb.data, "adm:ch"))
    add_row = [ibtn("افزودن کانال اجباری", callback_data="adm:ca", style=SUCCESS)]
    if not page.total:
        kb = InlineKeyboardMarkup(inline_keyboard=[add_row, [ibtn("بازگشت", callback_data=HOME, style=PRIMARY)]])
        await _view(
            cb,
            "📢 <b>کانال اجباری ورود به ربات</b>\n\nهنوز هیچ کانالی ثبت نشده.\n"
            "<i>تا کانالی ثبت نشود، همه بدون عضویت وارد ربات می‌شوند.</i>",
            kb,
        )
        return
    lines = [page_header("📢 کانال‌های اجباری ورود", page, empty=""), ""]
    rows_kb = [add_row]
    for r in page.items:
        title = (r.channel.title if r.channel else None) or str(r.channel_id)
        warn = ""
        if r.channel and not r.channel.bot_is_admin:
            warn = " ⚠️ ربات ادمین نیست"
        state = "فعال 🟢" if r.is_active else "خاموش ⚪️"
        lines.append(f"• <b>{esc(title)}</b> — {state}{warn}")
        rows_kb.append(
            [
                ibtn(
                    ("خاموش کردن " if r.is_active else "روشن کردن ") + title[:16],
                    callback_data=f"adm:ct:{r.id}",
                    style=DANGER if r.is_active else SUCCESS,
                ),
                ibtn("حذف", callback_data=f"adm:cd:{r.id}", style=DANGER),
            ]
        )
    await _view(cb, "\n".join(lines), paged_kb("adm:ch", page, extra_rows=rows_kb, back=HOME))


@router.callback_query(F.data == "adm:ca")
async def admin_channel_ask(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _guard(cb, db, db_user):
        return
    await state.set_state(AdminSG.channel_ref)
    await cb.message.answer(
        "📢 <b>افزودن کانال اجباری ورود</b>\n\n"
        "۱) دکمهٔ «افزودن ربات به کانال» را بزنید و کانال را انتخاب کنید.\n"
        "۲) بعد یک پست از آن کانال را همین‌جا فوروارد کنید، یا @username یا لینکش را بفرستید.",
        reply_markup=add_required_channel_kb(cancel=False),
    )
    await cb.answer()


@router.message(AdminSG.channel_ref)
async def admin_channel_add(message: Message, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ok(db, db_user):
        await _deny(message)
        return
    ref = extract_channel_ref(message)
    if ref is None:
        await message.answer(
            "کانال شناخته نشد. دکمهٔ افزودن ربات را بزنید یا یک پست از کانال را فوروارد کنید.\n"
            "برای خروج «لغو» بزنید.",
            reply_markup=add_required_channel_kb(cancel=False),
        )
        return
    try:
        await channel_svc.add_global_required_channel(db, message.bot, db_user.id, ref, scope="all")
    except AppError as exc:
        await message.answer(exc.message)
        return
    await state.clear()
    await message.answer("✅ کانال اجباری اضافه شد.", reply_markup=_back_kb("adm:ch:0"))


@router.callback_query(F.data.startswith("adm:ct:"))
async def admin_channel_toggle(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    row_id = _uuid_or_none(cb.data.split(":")[-1])
    row = await db.get(GlobalRequiredChannel, row_id) if row_id else None
    if not row:
        await cb.answer("یافت نشد", show_alert=True)
        return
    row.is_active = not row.is_active
    await write_audit(
        db,
        action="global_channel_toggled",
        entity_type="global_required_channel",
        entity_id=row.id,
        actor_id=db_user.id,
        extra={"is_active": row.is_active},
    )
    await cb.answer("فعال شد" if row.is_active else "خاموش شد")
    await admin_channels(cb, db, db_user)


@router.callback_query(F.data.startswith("adm:cd:"))
async def admin_channel_delete(cb: CallbackQuery, db: AsyncSession, db_user: User):
    """Toggling was the only option before, so a wrong channel stayed forever."""
    if not await _guard(cb, db, db_user):
        return
    row_id = _uuid_or_none(cb.data.split(":")[-1])
    row = await db.get(GlobalRequiredChannel, row_id) if row_id else None
    if not row:
        await cb.answer("یافت نشد", show_alert=True)
        return
    await db.delete(row)
    await write_audit(
        db,
        action="global_channel_removed",
        entity_type="global_required_channel",
        entity_id=row_id,
        actor_id=db_user.id,
    )
    await db.flush()
    await cb.answer("حذف شد")
    await admin_channels(cb, db, db_user)


# ---------------------------------------------------------------- winners


@router.callback_query(F.data.startswith("adm:win"))
async def admin_winners(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    rows = list(
        (
            await db.scalars(
                select(WinnerClaim)
                .options(selectinload(WinnerClaim.event), selectinload(WinnerClaim.user))
                .order_by(WinnerClaim.created_at.desc())
                .limit(60)
            )
        ).all()
    )
    rows.sort(key=lambda c: c.status != WinnerClaimStatus.PENDING)
    page = paginate(rows, parse_page(cb.data, "adm:win"))
    if not page.total:
        await _view(cb, page_header("🏆 ادعای برنده", page, empty="هنوز کسی ادعای برنده ثبت نکرده."), _back_kb())
        return
    flags = {"pending": "⏳", "approved": "✅", "rejected": "❌"}
    lines = [page_header("🏆 ادعای برنده", page, empty=""), ""]
    rows_kb = []
    for i, claim in enumerate(page.items, start=1):
        e = claim.event
        title = _event_label(e, 50) if e else "کاستوم حذف‌شده"
        lines.append(f"{i}. {flags.get(claim.status, '•')} <b>{esc(title)}</b>\n   {format_person(claim.user)}")
        rows_kb.append(
            [ibtn(f"دیدن اسکرین‌شات {i}", callback_data=f"adm:wv:{claim.id}", style=PRIMARY)]
        )
    await _view(cb, "\n".join(lines), paged_kb("adm:win", page, extra_rows=rows_kb, back=HOME))


@router.callback_query(F.data.startswith("adm:wv:"))
async def admin_winner_view(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    claim_id = _uuid_or_none(cb.data.split(":")[-1])
    claim = await db.get(WinnerClaim, claim_id) if claim_id else None
    if not claim:
        await cb.answer("یافت نشد", show_alert=True)
        return
    event = await db.get(Event, claim.event_id)
    player = await db.get(User, claim.user_id)
    prize = _event_label(event, 60) if event else "ادعای برنده"
    contact = await resolve_payout_contact(db, event) if event else None
    caption = (
        f"🏆 <b>{esc(prize)}</b>\n"
        f"کاستوم: {esc(event.title) if event else '—'}\n"
        f"بازیکن: {format_person(player)}\n"
        f"آیدی دریافت جایزه: {esc(contact) if contact else 'ثبت نشده'}\n"
        f"وضعیت: {esc(claim.status)}"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                ibtn("تأیید برنده", callback_data=f"adm:wok:{claim.id}", style=SUCCESS),
                ibtn("رد", callback_data=f"adm:wno:{claim.id}", style=DANGER),
            ],
            [ibtn("پیام به برنده", callback_data=f"orgw:msg:{claim.id}", style=PRIMARY)],
            [ibtn("بازگشت به فهرست", callback_data="adm:win:0", style=PRIMARY)],
        ]
    )
    try:
        await cb.message.answer_photo(claim.screenshot_file_id, caption=caption[:1024], reply_markup=kb)
        await cb.answer()
    except Exception:  # noqa: BLE001
        await _view(cb, caption + "\n\n<i>اسکرین‌شات قابل نمایش نیست.</i>", kb)


async def _resolve_winner(cb: CallbackQuery, db: AsyncSession, db_user: User, approved: bool) -> None:
    claim_id = _uuid_or_none(cb.data.split(":")[-1])
    claim = await db.get(WinnerClaim, claim_id) if claim_id else None
    if not claim:
        await cb.answer("یافت نشد", show_alert=True)
        return
    if claim.status != WinnerClaimStatus.PENDING:
        await cb.answer("این ادعا قبلاً بررسی شده است.", show_alert=True)
        return
    claim.status = WinnerClaimStatus.APPROVED if approved else WinnerClaimStatus.REJECTED
    claim.reviewed_by = db_user.id
    claim.reviewed_at = datetime.now(UTC)
    await write_audit(
        db,
        action="winner_claim_reviewed",
        entity_type="winner_claim",
        entity_id=claim.id,
        actor_id=db_user.id,
        extra={"approved": approved},
    )
    if approved and claim.organizer_id:
        org = await db.get(Organizer, claim.organizer_id)
        if org:
            await trust_svc.record(
                db,
                org,
                "prize_paid_confirmed",
                related_event_id=claim.event_id,
                actor_id=db_user.id,
            )
    player = await db.get(User, claim.user_id)
    event = await db.get(Event, claim.event_id)
    contact = await resolve_payout_contact(db, event) if (approved and event) else None
    if player and not player.is_bot_blocked:
        note = (
            format_payout_note(event, contact)
            if approved and event
            else "ادعای برنده بودن شما تأیید نشد."
        )
        try:
            await cb.bot.send_message(
                player.telegram_id,
                note,
                reply_markup=winner_reply_kb(str(claim.id), contact_url=contact_link(contact)),
            )
        except Exception:  # noqa: BLE001
            pass
    await db.flush()
    await cb.answer("ثبت شد")
    await cb.message.answer("✅ تأیید شد." if approved else "❌ رد شد.", reply_markup=_back_kb("adm:win:0"))


@router.callback_query(F.data.startswith("adm:wok:"))
async def admin_winner_ok(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    await _resolve_winner(cb, db, db_user, True)


@router.callback_query(F.data.startswith("adm:wno:"))
async def admin_winner_no(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    await _resolve_winner(cb, db, db_user, False)


# ---------------------------------------------------------------- reports


@router.callback_query(F.data.startswith("adm:rep"))
async def admin_reports(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    rows = (
        await db.scalars(
            select(Report).where(Report.status == ReportStatus.NEW).order_by(Report.created_at.desc()).limit(60)
        )
    ).all()
    page = paginate(rows, parse_page(cb.data, "adm:rep"))
    if not page.total:
        await _view(cb, page_header("⚠️ گزارش تخلف", page, empty="گزارش بازی وجود ندارد ✅"), _back_kb())
        return
    lines = [page_header("⚠️ گزارش‌های باز", page, empty=""), ""]
    rows_kb = []
    for i, r in enumerate(page.items, start=1):
        reporter = await db.get(User, r.reporter_id)
        body = (r.body or "").strip()
        lines.append(f"{i}. <b>{report_label(r.reason)}</b>\n   از: {format_person(reporter)}\n   {esc(body[:140])}")
        rows_kb.append(
            [ibtn(f"بررسی {i}: {report_label(r.reason)[:18]}", callback_data=f"adm:rv:{r.id}", style=DANGER)]
        )
    await _view(cb, "\n".join(lines), paged_kb("adm:rep", page, extra_rows=rows_kb, back=HOME))


@router.callback_query(F.data.startswith("adm:rv:"))
async def admin_report_view(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    report_id = _uuid_or_none(cb.data.split(":")[-1])
    r = await db.get(Report, report_id) if report_id else None
    if not r:
        await cb.answer("یافت نشد", show_alert=True)
        return
    event = await db.get(Event, r.event_id) if r.event_id else None
    reporter = await db.get(User, r.reporter_id)
    org = await db.get(Organizer, r.organizer_id) if r.organizer_id else None
    org_user = await db.get(User, org.user_id) if org else None
    when = format_local(event.starts_at, event.timezone) if event else "—"
    title = esc(event.title) if event else "— (پاک شده)"
    trust_line = f"{trust_svc.format_trust_line(org)}\n" if org else ""
    text = (
        f"⚠️ <b>{report_label(r.reason)}</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"کاستوم: {title}\n"
        f"ساعت: {when}\n"
        f"برگزارکننده: {format_person(org_user)}\n"
        f"{trust_line}"
        f"گزارش‌دهنده: {format_person(reporter)}\n"
        "━━━━━━━━━━━━━━\n"
        f"{esc((r.body or '')[:900])}"
    )
    buttons = []
    if org_user:
        buttons.append([ibtn("تأیید تخلف و کسر اعتبار", callback_data=f"adm:rup:{r.id}", style=DANGER)])
        buttons.append([ibtn("بن برگزاری این شخص", callback_data=f"adm:bno:{org_user.telegram_id}", style=DANGER)])
    buttons.append([ibtn("بستن بدون اقدام", callback_data=f"adm:rok:{r.id}", style=SUCCESS)])
    buttons.append([ibtn("بازگشت به فهرست", callback_data="adm:rep:0", style=PRIMARY)])
    await _view(cb, text, InlineKeyboardMarkup(inline_keyboard=buttons))


async def _close_report(db: AsyncSession, report: Report, actor_id, note: str) -> None:
    report.status = ReportStatus.CLOSED
    report.resolved_at = datetime.now(UTC)
    report.admin_note = note
    await write_audit(
        db,
        action="report_closed",
        entity_type="report",
        entity_id=report.id,
        actor_id=actor_id,
        extra={"note": note},
    )
    await db.flush()


@router.callback_query(F.data.startswith("adm:rok:"))
async def admin_report_ok(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    report_id = _uuid_or_none(cb.data.split(":")[-1])
    row = await db.get(Report, report_id) if report_id else None
    if not row:
        await cb.answer("یافت نشد", show_alert=True)
        return
    await _close_report(db, row, db_user.id, "بسته شد بدون اقدام")
    await cb.answer("بسته شد")
    await admin_reports(cb, db, db_user)


@router.callback_query(F.data.startswith("adm:rup:"))
async def admin_report_uphold(cb: CallbackQuery, db: AsyncSession, db_user: User):
    """Closing a report used to be the only option, and it changed nothing."""
    if not await _guard(cb, db, db_user):
        return
    report_id = _uuid_or_none(cb.data.split(":")[-1])
    row = await db.get(Report, report_id) if report_id else None
    if not row:
        await cb.answer("یافت نشد", show_alert=True)
        return
    org = await db.get(Organizer, row.organizer_id) if row.organizer_id else None
    score = None
    if org:
        rule = "prize_unpaid_reported" if row.reason == "unpaid_prize" else "report_upheld"
        score = await trust_svc.record(
            db,
            org,
            rule,
            related_event_id=row.event_id,
            actor_id=db_user.id,
            reason=f"گزارش تأییدشده: {report_label(row.reason)}",
        )
    await _close_report(db, row, db_user.id, "تخلف تأیید شد")
    await cb.answer("تخلف تأیید و اعتبار کسر شد")
    tail = f"\nاعتبار برگزارکننده الان: {int(round(score))}/100" if score is not None else ""
    await _view(cb, f"✅ تخلف تأیید شد و گزارش بسته شد.{tail}", _back_kb("adm:rep:0"))


# -------------------------------------------------------------- broadcast


async def _reachable_users(db: AsyncSession) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.deleted_at.is_(None), User.is_bot_blocked.is_(False))
        )
        or 0
    )


@router.callback_query(F.data == "adm:bc")
async def admin_bc_ask(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _guard(cb, db, db_user):
        return
    total = await _reachable_users(db)
    await state.set_state(AdminSG.broadcast_title)
    await _view(
        cb,
        "📣 <b>ارسال همگانی</b>\n\n"
        f"این پیام برای حدود <b>{total}</b> کاربر فرستاده می‌شود.\n\n"
        "اول یک <b>عنوان</b> کوتاه بفرستید — فقط برای بایگانی خودتان است و برای کاربرها ارسال نمی‌شود.",
        _back_kb(),
    )


@router.message(AdminSG.broadcast_title)
async def admin_bc_title(message: Message, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ok(db, db_user):
        await _deny(message)
        return
    title = (message.text or "").strip()
    if len(title) < 3:
        await message.answer("عنوان خیلی کوتاه است. برای خروج «لغو» بزنید.")
        return
    await state.update_data(title=title)
    await state.set_state(AdminSG.broadcast_body)
    await message.answer("حالا <b>متن پیامی</b> که کاربرها می‌بینند را بفرستید.")


@router.message(AdminSG.broadcast_body)
async def admin_bc_body(message: Message, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ok(db, db_user):
        await _deny(message)
        return
    body = (message.text or "").strip()
    if len(body) < 3:
        await message.answer("متن خیلی کوتاه است. برای خروج «لغو» بزنید.")
        return
    data = await state.get_data()
    row = BroadcastCampaign(title=data["title"], body=body, status="draft", created_by=db_user.id)
    db.add(row)
    await db.flush()
    await write_audit(db, action="broadcast_created", entity_type="broadcast", entity_id=row.id, actor_id=db_user.id)
    await state.clear()
    total = await _reachable_users(db)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn("تأیید و ارسال", callback_data=f"adm:bok:{row.id}", style=SUCCESS)],
            [ibtn("انصراف", callback_data=HOME, style=DANGER)],
        ]
    )
    await message.answer(
        "📋 <b>پیش‌نمایش ارسال همگانی</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"{esc(row.body)}\n"
        "━━━━━━━━━━━━━━\n"
        f"گیرندگان: حدود <b>{total}</b> نفر\n"
        "<i>این کار قابل بازگشت نیست.</i>",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("adm:bok:"))
async def admin_bc_confirm(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    row_id = _uuid_or_none(cb.data.split(":")[-1])
    row = await db.get(BroadcastCampaign, row_id) if row_id else None
    if not row:
        await cb.answer("یافت نشد", show_alert=True)
        return
    if row.status != "draft":
        # a second tap on the same button would send the whole campaign twice
        await cb.answer("این ارسال قبلاً تأیید شده است.", show_alert=True)
        return
    row.status = "running"
    row.confirmed_by = db_user.id
    row.confirmed_at = utcnow()
    await write_audit(
        db, action="broadcast_confirmed", entity_type="broadcast", entity_id=row.id, actor_id=db_user.id
    )
    await db.flush()
    from app.workers.enqueue import spawn
    from app.workers.tasks import run_broadcast

    spawn(run_broadcast, str(row.id))
    await cb.answer("شروع شد")
    await _view(cb, "📣 ارسال همگانی شروع شد. بسته به تعداد کاربران چند دقیقه طول می‌کشد.", _back_kb())


# --------------------------------------------------------- maintenance/cfg


@router.callback_query(F.data == "adm:mt")
async def admin_maintenance(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    current = bool(await settings_svc.get_setting(db, "maintenance_mode", False))
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                ibtn(
                    "خاموش کردن تعمیرات" if current else "روشن کردن تعمیرات",
                    callback_data="adm:mtx",
                    style=SUCCESS if current else DANGER,
                )
            ],
            [ibtn("بازگشت", callback_data=HOME, style=PRIMARY)],
        ]
    )
    state_text = (
        "<b>روشن</b> — همه به‌جز مدیر ربات قفل هستند."
        if current
        else "خاموش — ربات برای همه باز است."
    )
    await _view(
        cb,
        f"🔧 <b>حالت تعمیرات</b>\n\nوضعیت فعلی: {state_text}\n\n"
        "<i>وقتی روشن باشد هر کاربری که با ربات کار کند پیام «در حال تعمیرات» می‌گیرد.</i>",
        kb,
    )


@router.callback_query(F.data == "adm:mtx")
async def admin_maintenance_toggle(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    current = bool(await settings_svc.get_setting(db, "maintenance_mode", False))
    await settings_svc.set_setting(db, "maintenance_mode", not current, updated_by=db_user.id)
    await write_audit(
        db,
        action="setting_toggled",
        entity_type="setting",
        actor_id=db_user.id,
        extra={"key": "maintenance_mode", "value": not current},
    )
    await cb.answer("روشن شد" if not current else "خاموش شد")
    await admin_maintenance(cb, db, db_user)


TOGGLES = {
    "event_approval_required": (
        "تأیید دستی کاستوم",
        "اگر روشن باشد هر کاستوم قبل از دیده شدن باید توسط شما تأیید شود.",
        False,
    ),
    "auto_approve_organizers": (
        "تأیید خودکار برگزارکننده",
        "اگر روشن باشد هر کاربری بدون تأیید شما می‌تواند کاستوم بگذارد.",
        True,
    ),
}


@router.callback_query(F.data == "adm:cfg")
async def admin_cfg(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    rows = []
    lines = ["⚙️ <b>تنظیمات ربات</b>", ""]
    for key, (label, help_text, default) in TOGGLES.items():
        value = bool(await settings_svc.get_setting(db, key, default))
        lines.append(f"<b>{label}</b>: {'روشن 🟢' if value else 'خاموش ⚪️'}")
        lines.append(f"<i>{help_text}</i>")
        lines.append("")
        rows.append(
            [
                ibtn(
                    f"{'خاموش کردن' if value else 'روشن کردن'} {label}",
                    callback_data=f"adm:tg:{key}",
                    style=DANGER if value else SUCCESS,
                )
            ]
        )
    rows.append([ibtn("بازگشت", callback_data=HOME, style=PRIMARY)])
    await _view(cb, "\n".join(lines).strip(), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("adm:tg:"))
async def admin_toggle_setting(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    key = cb.data.split(":", 2)[-1]
    if key not in TOGGLES:
        await cb.answer("نامعتبر", show_alert=True)
        return
    default = TOGGLES[key][2]
    current = bool(await settings_svc.get_setting(db, key, default))
    await settings_svc.set_setting(db, key, not current, updated_by=db_user.id)
    await write_audit(
        db,
        action="setting_toggled",
        entity_type="setting",
        actor_id=db_user.id,
        extra={"key": key, "value": not current},
    )
    await cb.answer(f"{setting_fa(key)}: {'روشن' if not current else 'خاموش'}")
    await admin_cfg(cb, db, db_user)


# ---------------------------------------------------------- announcements


@router.callback_query(F.data.startswith("adm:ann"))
async def admin_anns(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    rows = (
        await db.scalars(
            select(CustomAnnouncement)
            .where(CustomAnnouncement.status == "published")
            .order_by(CustomAnnouncement.starts_at.asc())
            .limit(60)
        )
    ).all()
    page = paginate(rows, parse_page(cb.data, "adm:ann"))
    if not page.total:
        await _view(cb, page_header("📢 اطلاع‌رسانی‌ها", page, empty="اطلاع‌رسانی فعالی نیست."), _back_kb())
        return
    lines = [page_header("📢 اطلاع‌رسانی‌های فعال", page, empty=""), ""]
    rows_kb = []
    for row in page.items:
        lines.append(
            f"<b>{esc(row.channel_name)}</b>\n"
            f"{esc(row.channel_url or '—')}\n"
            f"🕐 {format_local(row.starts_at, row.timezone, compact=True)}"
        )
        lines.append("")
        rows_kb.append(
            [ibtn(f"مخفی کردن {row.channel_name[:18]}", callback_data=f"adm:ah:{row.id}", style=DANGER)]
        )
    await _view(cb, "\n".join(lines).strip(), paged_kb("adm:ann", page, extra_rows=rows_kb, back=HOME))


@router.callback_query(F.data.startswith("adm:ah:"))
async def admin_ann_hide(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    row_id = _uuid_or_none(cb.data.split(":")[-1])
    row = await db.get(CustomAnnouncement, row_id) if row_id else None
    if not row:
        await cb.answer("یافت نشد", show_alert=True)
        return
    await hide_announcement(db, row, db_user.id, "مخفی از پنل ادمین")
    await cb.answer("مخفی شد")
    await admin_anns(cb, db, db_user)


# ----------------------------------------------------------- recent users


@router.callback_query(F.data.startswith("adm:lu"))
async def admin_recent_users(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _guard(cb, db, db_user):
        return
    rows = (
        await db.scalars(
            select(User).where(User.deleted_at.is_(None)).order_by(User.created_at.desc()).limit(60)
        )
    ).all()
    page = paginate(rows, parse_page(cb.data, "adm:lu"))
    if not page.total:
        await _view(cb, page_header("👥 کاربران اخیر", page, empty="هنوز کاربری نیست."), _back_kb())
        return

    # two grouped queries instead of two per user
    ids = [u.id for u in page.items]
    hosted_rows = (
        await db.execute(
            select(Organizer.user_id, func.count(Event.id))
            .join(Event, Event.organizer_id == Organizer.id)
            .where(Organizer.user_id.in_(ids), Event.deleted_at.is_(None), Event.status != EventStatus.DRAFT)
            .group_by(Organizer.user_id)
        )
    ).all()
    hosted = {uid: int(n or 0) for uid, n in hosted_rows}
    joined_rows = (
        await db.execute(
            select(Registration.user_id, func.count())
            .where(Registration.user_id.in_(ids), Registration.status == RegistrationStatus.CONFIRMED)
            .group_by(Registration.user_id)
        )
    ).all()
    joined = {uid: int(n or 0) for uid, n in joined_rows}

    lines = [page_header("👥 کاربران اخیر", page, empty=""), ""]
    rows_kb = []
    for u in page.items:
        name = u.first_name or "-"
        lines.append(
            f"<b>{esc(name)}</b> @{esc(u.username or '-')} — <code>{u.telegram_id}</code>\n"
            f"کاستوم ثبت‌کرده: {hosted.get(u.id, 0)} | ثبت‌نام بازیکن: {joined.get(u.id, 0)}"
        )
        label = name if name != "-" else str(u.telegram_id)
        rows_kb.append([ibtn(f"پرونده {label[:18]}", callback_data=f"adm:uid:{u.telegram_id}", style=PRIMARY)])
    await _view(cb, "\n".join(lines).strip(), paged_kb("adm:lu", page, extra_rows=rows_kb, back=HOME))
