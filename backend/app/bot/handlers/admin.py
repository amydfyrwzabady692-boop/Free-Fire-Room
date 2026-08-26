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
from app.bot.keyboards.common import DANGER, PRIMARY, SUCCESS, add_required_channel_kb, ibtn, labeled
from app.bot.states.groups import AdminSG
from app.core.enums import BanScope, EventStatus, OrganizerStatus, RegistrationStatus, ReportStatus, UserStatus
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
from app.services import channels as channel_svc
from app.services import events as event_svc
from app.services import organizers as org_svc
from app.services import settings as settings_svc
from app.services.announcements import hide_announcement
from app.services.audit import write_audit
from app.services.reports import format_person, report_label
from app.locales.labels import ban_scope_fa, event_status_fa, setting_fa
from app.services.users import get_by_telegram

router = Router(name="admin")


def _admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn("داشبورد", callback_data="adm:dash", style=PRIMARY)],
            [
                ibtn("کاستوم‌های در انتظار", callback_data="adm:ev", style=SUCCESS),
                ibtn("برگزارکنندگان", callback_data="adm:org", style=PRIMARY),
            ],
            [
                ibtn("جستجوی کاربر", callback_data="adm:usr", style=PRIMARY),
                ibtn("کانال اجباری", callback_data="adm:ch", style=PRIMARY),
            ],
            [
                ibtn("گزارش تخلف", callback_data="adm:rep", style=DANGER),
                ibtn("برنده‌ها", callback_data="adm:win", style=SUCCESS),
            ],
            [ibtn("ارسال همگانی", callback_data="adm:bc", style=PRIMARY)],
            [
                ibtn("اطلاع‌رسانی‌ها", callback_data="adm:ann", style=PRIMARY),
                ibtn("کاربران اخیر", callback_data="adm:lu", style=PRIMARY),
            ],
            [ibtn("همه کاستوم‌ها و آمار", callback_data="adm:all", style=SUCCESS)],
            [ibtn("تنظیمات ربات", callback_data="adm:cfg", style=PRIMARY)],
            [ibtn("تعمیرات ربات", callback_data="adm:mt", style=DANGER)],
            [ibtn("منوی بازیکن", callback_data="adm:player", style=PRIMARY)],
        ]
    )


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[ibtn("بازگشت به پنل مالک ربات", callback_data="adm:home", style=PRIMARY)]]
    )


async def _deny(event: Message | CallbackQuery) -> None:
    text = "این بخش فقط برای مدیر ربات است."
    if isinstance(event, CallbackQuery):
        await event.answer(text, show_alert=True)
        return
    await event.answer(text)


async def _ok(db: AsyncSession, user: User | None) -> bool:
    return await is_active_admin(db, user)


async def _show_home(message: Message) -> None:
    await message.answer(
        "👑 پنل مالک ربات — فقط برای صاحب همین ربات.\n"
        "گزارش تخلف، بن، کانال اجباری ورود، تنظیمات.\n"
        "پنل برگزارکننده جداست و برای کاربرانی است که کاستوم جایزه‌دار می‌گذارند.",
        reply_markup=_admin_kb(),
    )


@router.message(Command("admin"))
@router.message(F.text.in_(labeled("پنل ادمین", "پنل مالک ربات")))
async def admin_home(message: Message, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ok(db, db_user):
        await _deny(message)
        return
    await state.clear()
    await _show_home(message)


@router.callback_query(F.data == "adm:home")
async def admin_home_cb(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    await state.clear()
    await replace_callback_view(cb, "👑 پنل مالک ربات — فقط برای صاحب همین ربات.", inline=_admin_kb())


@router.callback_query(F.data == "adm:player")
async def admin_to_player(cb: CallbackQuery, db: AsyncSession, db_user: User):
    await replace_callback_view(cb, "منوی بازیکن", menu=await menu_for(db, db_user))


@router.callback_query(F.data == "adm:dash")
async def admin_dash(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    now = utcnow()
    day = now - timedelta(days=1)
    users = await db.scalar(select(func.count()).select_from(User).where(User.deleted_at.is_(None)))
    new_users = await db.scalar(select(func.count()).select_from(User).where(User.created_at >= day))
    banned = await db.scalar(select(func.count()).select_from(Ban).where(Ban.is_active.is_(True)))
    orgs = await db.scalar(select(func.count()).select_from(Organizer))
    pending_orgs = await db.scalar(
        select(func.count()).select_from(Organizer).where(Organizer.status == OrganizerStatus.PENDING)
    )
    pending_events = await db.scalar(
        select(func.count()).select_from(Event).where(Event.status == EventStatus.PENDING_APPROVAL)
    )
    events_active = await db.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.status.in_([EventStatus.PUBLISHED, EventStatus.FULL, EventStatus.STARTED]))
    )
    regs = await db.scalar(
        select(func.count()).select_from(Registration).where(Registration.status == RegistrationStatus.CONFIRMED)
    )
    sent = await db.scalar(select(func.count()).select_from(Delivery).where(Delivery.status == "sent"))
    anns = await db.scalar(
        select(func.count()).select_from(CustomAnnouncement).where(CustomAnnouncement.status == "published")
    )
    maint = await settings_svc.get_setting(db, "maintenance_mode", False)
    hosted = await db.scalar(
        select(func.count()).select_from(Event).where(Event.deleted_at.is_(None), Event.status != EventStatus.DRAFT)
    )
    await cb.message.answer(
        "<b>داشبورد مالک ربات</b>\n"
        f"کاربران: {users} (۲۴ساعت: {new_users})\n"
        f"بن‌شده: {banned}\n"
        f"برگزارکننده: {orgs} (در انتظار: {pending_orgs})\n"
        f"کاستوم ثبت‌شده: {hosted}\n"
        f"کاستوم فعال: {events_active} | در انتظار تأیید: {pending_events}\n"
        f"ثبت‌نام قطعی بازیکن‌ها: {regs}\n"
        f"ارسال مشخصات موفق: {sent}\n"
        f"اطلاع‌رسانی فعال: {anns}\n"
        f"حالت تعمیرات: {'روشن' if maint else 'خاموش'}",
        reply_markup=_back_kb(),
    )
    await cb.answer()


@router.callback_query(F.data == "adm:all")
async def admin_all_events(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    from app.services.reviews import event_audience_stats, format_audience_stats

    rows = (
        await db.scalars(
            select(Event)
            .where(Event.deleted_at.is_(None), Event.status != EventStatus.DRAFT)
            .options(selectinload(Event.organizer))
            .order_by(Event.starts_at.desc())
            .limit(20)
        )
    ).all()
    if not rows:
        await cb.message.answer("کاستومی ثبت نشده.", reply_markup=_back_kb())
        await cb.answer()
        return
    await cb.message.answer(f"آخرین {len(rows)} کاستوم ثبت‌شده:")
    for i, e in enumerate(rows):
        stats = await event_audience_stats(db, e.id)
        org = e.organizer.display_name if e.organizer else "-"
        prize = (e.prize_summary or "").strip() or "—"
        await cb.message.answer(
            f"🎮 <b>{esc(e.title)}</b>\n"
            f"🎁 جایزه: {esc(prize)}\n"
            f"🕐 {format_local(e.starts_at, e.timezone)}\n"
            f"برگزارکننده: {esc(org)}\n"
            f"وضعیت: {event_status_fa(e.status)}\n"
            f"{format_audience_stats(stats)}",
            reply_markup=_back_kb() if i == len(rows) - 1 else None,
        )
    await cb.answer()


@router.callback_query(F.data == "adm:ev")
async def admin_events(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    rows = (
        await db.scalars(
            select(Event)
            .where(Event.status == EventStatus.PENDING_APPROVAL, Event.deleted_at.is_(None))
            .options(selectinload(Event.organizer))
            .order_by(Event.created_at.desc())
            .limit(10)
        )
    ).all()
    if not rows:
        await cb.message.answer("کاستوم در انتظار تأیید نیست.", reply_markup=_back_kb())
        await cb.answer()
        return
    for e in rows:
        org = e.organizer.display_name if e.organizer else "-"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    ibtn("تأیید و انتشار", callback_data=f"adm:ea:{e.id}", style=SUCCESS),
                    ibtn("رد", callback_data=f"adm:er:{e.id}", style=DANGER),
                ],
                [ibtn("بازگشت", callback_data="adm:home", style=PRIMARY)],
            ]
        )
        await cb.message.answer(
            f"<b>{esc(e.title)}</b>\n"
            f"🎁 جایزه: {esc((e.prize_summary or '').strip() or '—')}\n"
            f"برگزارکننده: {esc(org)}\n"
            f"ساعت: {format_local(e.starts_at, e.timezone)}\n"
            f"وضعیت: {event_status_fa(e.status)}",
            reply_markup=kb,
        )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:ea:"))
async def admin_event_approve(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    event = await db.get(Event, UUID(cb.data.split(":")[-1]))
    if not event:
        await cb.answer("یافت نشد", show_alert=True)
        return
    try:
        await event_svc.approve_event(db, event, db_user.id)
        await cb.message.answer(f"کاستوم «{esc(event.title)}» منتشر شد.", reply_markup=_back_kb())
    except AppError as exc:
        await cb.message.answer(exc.message, reply_markup=_back_kb())
    await cb.answer()


@router.callback_query(F.data.startswith("adm:er:"))
async def admin_event_reject(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    event = await db.get(Event, UUID(cb.data.split(":")[-1]))
    if not event:
        await cb.answer("یافت نشد", show_alert=True)
        return
    try:
        await event_svc.reject_event(db, event, db_user.id, "رد از پنل ربات")
        await cb.message.answer(f"کاستوم «{esc(event.title)}» رد شد.", reply_markup=_back_kb())
    except AppError as exc:
        await cb.message.answer(exc.message, reply_markup=_back_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:org")
async def admin_orgs(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    pending = (
        await db.scalars(
            select(Organizer)
            .options(selectinload(Organizer.user))
            .where(Organizer.status == OrganizerStatus.PENDING)
            .order_by(Organizer.created_at.desc())
            .limit(10)
        )
    ).all()
    for org in pending:
        u = org.user
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    ibtn("تأیید", callback_data=f"adm:oa:{org.id}", style=SUCCESS),
                    ibtn("رد", callback_data=f"adm:oj:{org.id}", style=DANGER),
                ]
            ]
        )
        await cb.message.answer(
            f"در انتظار تأیید\n{esc(org.display_name or '-')}\nتلگرام: {u.telegram_id if u else '-'}",
            reply_markup=kb,
        )

    rows = (
        await db.scalars(
            select(Organizer)
            .options(selectinload(Organizer.user))
            .order_by(Organizer.created_at.desc())
            .limit(20)
        )
    ).all()
    if not rows and not pending:
        await cb.message.answer("برگزارکننده‌ای ثبت نشده.", reply_markup=_back_kb())
        await cb.answer()
        return
    hosted_rows = (
        await db.execute(
            select(Event.organizer_id, func.count())
            .where(Event.deleted_at.is_(None), Event.status != EventStatus.DRAFT)
            .group_by(Event.organizer_id)
        )
    ).all()
    hosted_map = {oid: int(n or 0) for oid, n in hosted_rows}
    text = "برگزارکننده‌ها و تعداد ثبت کاستوم:\n"
    for org in rows:
        u = org.user
        name = org.display_name or (u.first_name if u else "-")
        tg = u.telegram_id if u else "-"
        last = await db.scalar(
            select(Event)
            .where(Event.organizer_id == org.id, Event.deleted_at.is_(None))
            .order_by(Event.starts_at.desc())
        )
        prize = (last.prize_summary or "").strip() if last else ""
        prize_line = f" | آخرین جایزه: {prize[:40]}" if prize else ""
        text += (
            f"• {esc(name)} | {tg} | ثبت کاستوم: {hosted_map.get(org.id, 0)}"
            f"{esc(prize_line)}\n"
        )
    await cb.message.answer(text, reply_markup=_back_kb())
    await cb.answer()


@router.callback_query(F.data.startswith("adm:oa:"))
async def admin_org_approve(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    org = await db.get(Organizer, UUID(cb.data.split(":")[-1]))
    if not org:
        await cb.answer("یافت نشد", show_alert=True)
        return
    await org_svc.approve_organizer(db, org, db_user.id, verified=True)
    await cb.message.answer("برگزارکننده تأیید شد.", reply_markup=_back_kb())
    await cb.answer()


@router.callback_query(F.data.startswith("adm:oj:"))
async def admin_org_reject(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    org = await db.get(Organizer, UUID(cb.data.split(":")[-1]))
    if not org:
        await cb.answer("یافت نشد", show_alert=True)
        return
    await org_svc.reject_organizer(db, org, db_user.id, "رد از پنل ربات")
    await cb.message.answer("درخواست برگزارکننده رد شد.", reply_markup=_back_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:usr")
async def admin_user_ask(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    await state.set_state(AdminSG.user_query)
    await cb.message.answer("شناسه عددی تلگرام کاربر را بفرستید.", reply_markup=_back_kb())
    await cb.answer()


@router.message(AdminSG.user_query)
async def admin_user_show(message: Message, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ok(db, db_user):
        await _deny(message)
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("فقط عدد شناسه تلگرام را بفرستید.")
        return
    target = await get_by_telegram(db, int(raw))
    if not target:
        await message.answer("کاربر یافت نشد. اول باید ربات را /start کرده باشد.")
        return
    await state.update_data(target_tg=target.telegram_id)
    banned = await db.scalar(
        select(Ban).where(Ban.user_id == target.id, Ban.is_active.is_(True)).order_by(Ban.created_at.desc())
    )
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
            .where(
                Registration.user_id == target.id,
                Registration.source == "deep_link",
            )
        )
        or 0
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
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn("بن ربات", callback_data=f"adm:bn:{target.telegram_id}", style=DANGER)],
            [ibtn("بن برگزاری", callback_data=f"adm:bno:{target.telegram_id}", style=DANGER)],
            [ibtn("رفع بن", callback_data=f"adm:ub:{target.telegram_id}", style=SUCCESS)],
            [ibtn("بازگشت", callback_data="adm:home", style=PRIMARY)],
        ]
    )
    extra = ""
    for e in last_events:
        extra += (
            f"\n• {format_local(e.starts_at, e.timezone, compact=True)}"
            f" | {esc((e.prize_summary or e.title or '—')[:50])}"
            f" | {event_status_fa(e.status)}"
        )
    await message.answer(
        f"نام: {esc(target.first_name or '-')}\n"
        f"یوزرنیم: @{esc(target.username or '-')}\n"
        f"شناسه: {target.telegram_id}\n"
        f"کاستوم ثبت‌کرده: {hosted}\n"
        f"ثبت‌نام قطعی به‌عنوان بازیکن: {joined}\n"
        f"از لینک اختصاصی آمده: {from_link}\n"
        f"وضعیت بن: {esc(banned.reason) if banned else 'آزاد'}"
        + (f"\n\nآخرین کاستوم‌هایش:{extra}" if extra else ""),
        reply_markup=kb,
    )
    await state.clear()


@router.callback_query(F.data.startswith("adm:bn:") | F.data.startswith("adm:bno:"))
async def admin_ban_ask(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    parts = cb.data.split(":")
    scope = BanScope.BOT if parts[1] == "bn" else BanScope.ORGANIZE
    await state.set_state(AdminSG.ban_reason)
    await state.update_data(target_tg=int(parts[-1]), ban_scope=scope)
    label = "ربات" if scope == BanScope.BOT else "برگزاری / اطلاع‌رسانی"
    await cb.message.answer(f"دلیل بن {label} را بنویسید (حداقل ۳ حرف).")
    await cb.answer()


@router.message(AdminSG.ban_reason)
async def admin_ban_do(message: Message, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ok(db, db_user):
        await _deny(message)
        return
    reason = (message.text or "").strip()
    if len(reason) < 3:
        await message.answer("دلیل خیلی کوتاه است.")
        return
    data = await state.get_data()
    target = await get_by_telegram(db, int(data["target_tg"]))
    if not target:
        await message.answer("کاربر یافت نشد.")
        await state.clear()
        return
    scope = data.get("ban_scope") or BanScope.BOT
    db.add(
        Ban(
            user_id=target.id,
            scope=scope,
            reason=reason,
            is_active=True,
            created_by=db_user.id,
        )
    )
    if scope == BanScope.BOT:
        target.status = UserStatus.BANNED
    await write_audit(
        db, action="user_banned", entity_type="user", entity_id=target.id, actor_id=db_user.id, extra={"reason": reason, "scope": scope}
    )
    await state.clear()
    await message.answer(
        f"کاربر {target.telegram_id} بن شد ({ban_scope_fa(scope)}).",
        reply_markup=_back_kb(),
    )


@router.callback_query(F.data.startswith("adm:ub:"))
async def admin_unban(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    target = await get_by_telegram(db, int(cb.data.split(":")[-1]))
    if not target:
        await cb.answer("یافت نشد", show_alert=True)
        return
    rows = (
        await db.scalars(select(Ban).where(Ban.user_id == target.id, Ban.is_active.is_(True)))
    ).all()
    for row in rows:
        row.is_active = False
    target.status = UserStatus.ACTIVE
    await write_audit(db, action="user_unbanned", entity_type="user", entity_id=target.id, actor_id=db_user.id)
    await cb.message.answer("بن برداشته شد.", reply_markup=_back_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:ch")
async def admin_channels(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    rows = (
        await db.scalars(
            select(GlobalRequiredChannel).options(selectinload(GlobalRequiredChannel.channel)).order_by(
                GlobalRequiredChannel.sort_order
            )
        )
    ).all()
    buttons = [[ibtn("افزودن کانال اجباری", callback_data="adm:ca", style=SUCCESS)]]
    if not rows:
        buttons.append([ibtn("بازگشت", callback_data="adm:home", style=PRIMARY)])
        await cb.message.answer("کانال اجباری ثبت نشده.", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        await cb.answer()
        return
    text = "کانال‌های اجباری:\n"
    for r in rows:
        title = r.channel.title if r.channel else str(r.channel_id)
        flag = "فعال" if r.is_active else "خاموش"
        text += f"• {title} — {flag}\n"
        buttons.append(
            [
                ibtn(
                    f"{'خاموش' if r.is_active else 'روشن'} کردن {title[:20]}",
                    callback_data=f"adm:ct:{r.id}",
                    style=DANGER if r.is_active else SUCCESS,
                )
            ]
        )
    buttons.append([ibtn("بازگشت", callback_data="adm:home", style=PRIMARY)])
    await cb.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data == "adm:ca")
async def admin_channel_ask(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    await state.set_state(AdminSG.channel_ref)
    await cb.message.answer(
        "کانال اجباری ورود به ربات را وصل کنید.\n\n"
        "دکمه «افزودن ربات به کانال» را بزنید، بعد یک پست از کانال را فوروارد کنید یا @username / لینک را بفرستید.",
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
            "کانال شناخته نشد. دکمه افزودن ربات را بزنید یا یک پست از کانال را فوروارد کنید.",
            reply_markup=add_required_channel_kb(cancel=False),
        )
        return
    try:
        await channel_svc.add_global_required_channel(db, message.bot, db_user.id, ref, scope="all")
        await message.answer("کانال اجباری اضافه شد.", reply_markup=_back_kb())
        await state.clear()
    except AppError as exc:
        await message.answer(exc.message)


@router.callback_query(F.data.startswith("adm:ct:"))
async def admin_channel_toggle(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    row = await db.get(GlobalRequiredChannel, UUID(cb.data.split(":")[-1]))
    if not row:
        await cb.answer("یافت نشد", show_alert=True)
        return
    row.is_active = not row.is_active
    await write_audit(
        db, action="global_channel_toggled", entity_type="global_required_channel", entity_id=row.id, actor_id=db_user.id
    )
    await cb.message.answer(f"وضعیت کانال: {'فعال' if row.is_active else 'خاموش'}", reply_markup=_back_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:win")
async def admin_winners(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    from app.services.winners import list_winner_claims

    rows = await list_winner_claims(db)
    if not rows:
        await cb.message.answer("ادعای برنده‌ای ثبت نشده.", reply_markup=_back_kb())
        await cb.answer()
        return
    await cb.message.answer(f"آخرین {len(rows)} ادعای برنده:")
    for claim in rows:
        e = claim.event
        player = claim.user
        prize = (e.prize_summary if e else "") or "—"
        text = (
            f"🏆 <b>{esc(e.title) if e else 'کاستوم'}</b>\n"
            f"جایزه: {esc(prize)}\n"
            f"بازیکن: {format_person(player)}\n"
            f"وضعیت: {esc(claim.status)}"
        )
        try:
            await cb.message.answer_photo(claim.screenshot_file_id, caption=text[:1024])
        except Exception:
            await cb.message.answer(text)
    await cb.message.answer("بازگشت:", reply_markup=_back_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:rep")
async def admin_reports(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    rows = (
        await db.scalars(select(Report).where(Report.status == ReportStatus.NEW).order_by(Report.created_at.desc()).limit(15))
    ).all()
    if not rows:
        await cb.message.answer("گزارش جدیدی نیست.", reply_markup=_back_kb())
        await cb.answer()
        return
    for r in rows:
        event = await db.get(Event, r.event_id) if r.event_id else None
        reporter = await db.get(User, r.reporter_id)
        org = await db.get(Organizer, r.organizer_id) if r.organizer_id else None
        org_user = await db.get(User, org.user_id) if org else None
        when = format_local(event.starts_at, event.timezone) if event else "-"
        text = (
            f"<b>{report_label(r.reason)}</b>\n"
            f"کاستوم: {esc(event.title) if event else '-'}\n"
            f"ساعت: {when}\n"
            f"برگزارکننده: {format_person(org_user)}\n"
            f"گزارش‌دهنده: {format_person(reporter)}\n\n"
            f"{esc((r.body or '')[:400])}"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[ibtn("بسته شد", callback_data=f"adm:rok:{r.id}", style=SUCCESS)]]
        )
        await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:rok:"))
async def admin_report_ok(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    row = await db.get(Report, UUID(cb.data.split(":")[-1]))
    if not row:
        await cb.answer("یافت نشد", show_alert=True)
        return
    row.status = ReportStatus.CLOSED
    row.resolved_at = datetime.now(UTC)
    row.admin_note = "از پنل ربات"
    await cb.message.answer("گزارش بسته شد.", reply_markup=_back_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:bc")
async def admin_bc_ask(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    await state.set_state(AdminSG.broadcast_title)
    await cb.message.answer("عنوان ارسال همگانی را بفرستید.")
    await cb.answer()


@router.message(AdminSG.broadcast_title)
async def admin_bc_title(message: Message, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ok(db, db_user):
        await _deny(message)
        return
    title = (message.text or "").strip()
    if len(title) < 3:
        await message.answer("عنوان خیلی کوتاه است.")
        return
    await state.update_data(title=title)
    await state.set_state(AdminSG.broadcast_body)
    await message.answer("متن پیام همگانی را بفرستید.")


@router.message(AdminSG.broadcast_body)
async def admin_bc_body(message: Message, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ok(db, db_user):
        await _deny(message)
        return
    body = (message.text or "").strip()
    if len(body) < 3:
        await message.answer("متن خیلی کوتاه است.")
        return
    data = await state.get_data()
    row = BroadcastCampaign(title=data["title"], body=body, status="draft", created_by=db_user.id)
    db.add(row)
    await db.flush()
    await write_audit(db, action="broadcast_created", entity_type="broadcast", entity_id=row.id, actor_id=db_user.id)
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn("تأیید و ارسال", callback_data=f"adm:bok:{row.id}", style=SUCCESS)],
            [ibtn("انصراف", callback_data="adm:home", style=DANGER)],
        ]
    )
    await message.answer(f"پیش‌نویس آماده است:\n<b>{esc(row.title)}</b>\n{esc(row.body)}", reply_markup=kb)


@router.callback_query(F.data.startswith("adm:bok:"))
async def admin_bc_confirm(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    row = await db.get(BroadcastCampaign, UUID(cb.data.split(":")[-1]))
    if not row:
        await cb.answer("یافت نشد", show_alert=True)
        return
    row.status = "running"
    row.confirmed_by = db_user.id
    row.confirmed_at = utcnow()
    await write_audit(db, action="broadcast_confirmed", entity_type="broadcast", entity_id=row.id, actor_id=db_user.id)
    from app.workers.enqueue import spawn
    from app.workers.tasks import run_broadcast

    spawn(run_broadcast, str(row.id))
    await cb.message.answer("ارسال همگانی شروع شد.", reply_markup=_back_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:mt")
async def admin_maintenance(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    current = bool(await settings_svc.get_setting(db, "maintenance_mode", False))
    await settings_svc.set_setting(db, "maintenance_mode", not current, updated_by=db_user.id)
    await cb.message.answer(
        f"حالت تعمیرات {'روشن' if not current else 'خاموش'} شد.",
        reply_markup=_back_kb(),
    )
    await cb.answer()


@router.callback_query(F.data == "adm:ann")
async def admin_anns(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    rows = (
        await db.scalars(
            select(CustomAnnouncement)
            .where(CustomAnnouncement.status == "published")
            .order_by(CustomAnnouncement.starts_at.asc())
            .limit(12)
        )
    ).all()
    if not rows:
        await cb.message.answer("اطلاع‌رسانی فعالی نیست.", reply_markup=_back_kb())
        await cb.answer()
        return
    for row in rows:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[ibtn("مخفی کردن", callback_data=f"adm:ah:{row.id}", style=DANGER)]]
        )
        await cb.message.answer(
            f"کانال: {esc(row.channel_name)}\n"
            f"لینک: {esc(row.channel_url or '—')}\n"
            f"ساعت: {format_local(row.starts_at, row.timezone)}",
            reply_markup=kb,
        )
    await cb.message.answer("بازگشت:", reply_markup=_back_kb())
    await cb.answer()


@router.callback_query(F.data.startswith("adm:ah:"))
async def admin_ann_hide(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    row = await db.get(CustomAnnouncement, UUID(cb.data.split(":")[-1]))
    if not row:
        await cb.answer("یافت نشد", show_alert=True)
        return
    await hide_announcement(db, row, db_user.id, "مخفی از پنل ادمین")
    await cb.message.answer("اطلاع‌رسانی مخفی شد.", reply_markup=_back_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:lu")
async def admin_recent_users(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    rows = (
        await db.scalars(select(User).where(User.deleted_at.is_(None)).order_by(User.created_at.desc()).limit(15))
    ).all()
    if not rows:
        await cb.message.answer("کاربری نیست.", reply_markup=_back_kb())
        await cb.answer()
        return
    text = "کاربران اخیر:\n"
    for u in rows:
        hosted = int(
            await db.scalar(
                select(func.count())
                .select_from(Event)
                .join(Organizer, Organizer.id == Event.organizer_id)
                .where(Organizer.user_id == u.id, Event.deleted_at.is_(None), Event.status != EventStatus.DRAFT)
            )
            or 0
        )
        joined = int(
            await db.scalar(
                select(func.count())
                .select_from(Registration)
                .where(Registration.user_id == u.id, Registration.status == RegistrationStatus.CONFIRMED)
            )
            or 0
        )
        text += (
            f"• {esc(u.first_name or '-')} | {u.telegram_id}\n"
            f"  ثبت کاستوم: {hosted} | ثبت‌نام بازیکن: {joined}\n"
        )
    await cb.message.answer(text + "\nبرای بن/رفع بن از «جستجوی کاربر» شناسه را بفرستید.", reply_markup=_back_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:cfg")
async def admin_cfg(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    approval = bool(await settings_svc.get_setting(db, "event_approval_required", False))
    auto_org = bool(await settings_svc.get_setting(db, "auto_approve_organizers", True))
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                ibtn(
                    f"تأیید کاستوم: {'روشن' if approval else 'خاموش'}",
                    callback_data="adm:tg:event_approval_required",
                    style=SUCCESS if approval else PRIMARY,
                )
            ],
            [
                ibtn(
                    f"تأیید خودکار برگزارکننده: {'روشن' if auto_org else 'خاموش'}",
                    callback_data="adm:tg:auto_approve_organizers",
                    style=SUCCESS if auto_org else PRIMARY,
                )
            ],
            [ibtn("بازگشت", callback_data="adm:home", style=PRIMARY)],
        ]
    )
    await cb.message.answer(
        "تنظیمات ربات:\n"
        "اگر تأیید کاستوم خاموش باشد، کاستوم جایزه‌دار بلافاصله در فهرست می‌آید.\n"
        "اگر تأیید خودکار برگزارکننده روشن باشد، هر کاربر می‌تواند کاستوم خودش را بسازد.",
        reply_markup=kb,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("adm:tg:"))
async def admin_toggle_setting(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    key = cb.data.split(":", 2)[-1]
    if key not in {"event_approval_required", "auto_approve_organizers"}:
        await cb.answer("نامعتبر", show_alert=True)
        return
    current = bool(await settings_svc.get_setting(db, key, False))
    await settings_svc.set_setting(db, key, not current, updated_by=db_user.id)
    await write_audit(db, action="setting_toggled", entity_type="setting", actor_id=db_user.id, extra={"key": key, "value": not current})
    await cb.message.answer(
        f"{setting_fa(key)} = {'روشن' if not current else 'خاموش'}",
        reply_markup=_back_kb(),
    )
    await cb.answer()

