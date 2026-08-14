from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.access import is_active_admin, menu_for
from app.bot.states.groups import AdminSG
from app.core.enums import BanScope, EventStatus, OrganizerStatus, RegistrationStatus
from app.core.errors import AppError
from app.core.time import utcnow
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
from app.services.audit import write_audit
from app.services.users import get_by_telegram

router = Router(name="admin")


def _admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="داشبورد", callback_data="adm:dash")],
            [
                InlineKeyboardButton(text="کاستوم‌های در انتظار", callback_data="adm:ev"),
                InlineKeyboardButton(text="برگزارکنندگان", callback_data="adm:org"),
            ],
            [
                InlineKeyboardButton(text="جستجوی کاربر", callback_data="adm:usr"),
                InlineKeyboardButton(text="کانال اجباری", callback_data="adm:ch"),
            ],
            [
                InlineKeyboardButton(text="گزارش تخلف", callback_data="adm:rep"),
                InlineKeyboardButton(text="ارسال همگانی", callback_data="adm:bc"),
            ],
            [InlineKeyboardButton(text="تعمیرات ربات", callback_data="adm:mt")],
            [InlineKeyboardButton(text="منوی بازیکن", callback_data="adm:player")],
        ]
    )


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="بازگشت به پنل ادمین", callback_data="adm:home")]]
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
        "پنل مدیریت ربات\nاز دکمه‌های زیر استفاده کنید. مرورگر لازم نیست.",
        reply_markup=_admin_kb(),
    )


@router.message(Command("admin"))
@router.message(F.text == "پنل ادمین")
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
    await cb.message.answer("پنل مدیریت ربات", reply_markup=_admin_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:player")
async def admin_to_player(cb: CallbackQuery, db: AsyncSession, db_user: User):
    await cb.message.answer("منوی بازیکن", reply_markup=await menu_for(db, db_user))
    await cb.answer()


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
    maint = await settings_svc.get_setting(db, "maintenance_mode", False)
    await cb.message.answer(
        "<b>داشبورد</b>\n"
        f"کاربران: {users} (۲۴ساعت: {new_users})\n"
        f"بن‌شده: {banned}\n"
        f"برگزارکننده: {orgs} (در انتظار: {pending_orgs})\n"
        f"کاستوم فعال: {events_active} | در انتظار تأیید: {pending_events}\n"
        f"ثبت‌نام قطعی: {regs}\n"
        f"ارسال مشخصات موفق: {sent}\n"
        f"حالت تعمیرات: {'روشن' if maint else 'خاموش'}",
        reply_markup=_back_kb(),
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
                    InlineKeyboardButton(text="تأیید و انتشار", callback_data=f"adm:ea:{e.id}"),
                    InlineKeyboardButton(text="رد", callback_data=f"adm:er:{e.id}"),
                ],
                [InlineKeyboardButton(text="بازگشت", callback_data="adm:home")],
            ]
        )
        await cb.message.answer(
            f"<b>{e.title}</b>\nبرگزارکننده: {org}\nظرفیت: {e.capacity}\nوضعیت: {e.status}",
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
        await cb.message.answer(f"کاستوم «{event.title}» منتشر شد.", reply_markup=_back_kb())
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
        await cb.message.answer(f"کاستوم «{event.title}» رد شد.", reply_markup=_back_kb())
    except AppError as exc:
        await cb.message.answer(exc.message, reply_markup=_back_kb())
    await cb.answer()


@router.callback_query(F.data == "adm:org")
async def admin_orgs(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    rows = (
        await db.scalars(
            select(Organizer)
            .options(selectinload(Organizer.user))
            .where(Organizer.status == OrganizerStatus.PENDING)
            .order_by(Organizer.created_at.desc())
            .limit(10)
        )
    ).all()
    if not rows:
        await cb.message.answer("درخواست برگزارکننده در انتظار نیست.", reply_markup=_back_kb())
        await cb.answer()
        return
    for org in rows:
        u = org.user
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="تأیید", callback_data=f"adm:oa:{org.id}"),
                    InlineKeyboardButton(text="رد", callback_data=f"adm:oj:{org.id}"),
                ]
            ]
        )
        await cb.message.answer(
            f"{org.display_name or '-'}\nتلگرام: {u.telegram_id if u else '-'}\nوضعیت: {org.status}",
            reply_markup=kb,
        )
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
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="بن ربات", callback_data=f"adm:bn:{target.telegram_id}")],
            [InlineKeyboardButton(text="رفع بن", callback_data=f"adm:ub:{target.telegram_id}")],
            [InlineKeyboardButton(text="بازگشت", callback_data="adm:home")],
        ]
    )
    await message.answer(
        f"نام: {target.first_name or '-'}\n"
        f"یوزرنیم: @{target.username or '-'}\n"
        f"شناسه: {target.telegram_id}\n"
        f"وضعیت بن: {banned.reason if banned else 'آزاد'}",
        reply_markup=kb,
    )
    await state.clear()


@router.callback_query(F.data.startswith("adm:bn:"))
async def admin_ban_ask(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    await state.set_state(AdminSG.ban_reason)
    await state.update_data(target_tg=int(cb.data.split(":")[-1]))
    await cb.message.answer("دلیل بن را بنویسید (حداقل ۳ حرف).")
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
    db.add(
        Ban(
            user_id=target.id,
            scope=BanScope.BOT,
            reason=reason,
            is_active=True,
            created_by=db_user.id,
        )
    )
    await write_audit(
        db, action="user_banned", entity_type="user", entity_id=target.id, actor_id=db_user.id, extra={"reason": reason}
    )
    await state.clear()
    await message.answer(f"کاربر {target.telegram_id} بن شد.", reply_markup=_back_kb())


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
    buttons = [[InlineKeyboardButton(text="افزودن کانال اجباری", callback_data="adm:ca")]]
    if not rows:
        buttons.append([InlineKeyboardButton(text="بازگشت", callback_data="adm:home")])
        await cb.message.answer("کانال اجباری ثبت نشده.", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
        await cb.answer()
        return
    text = "کانال‌های اجباری:\n"
    for r in rows:
        title = r.channel.title if r.channel else str(r.channel_id)
        flag = "فعال" if r.is_active else "خاموش"
        text += f"• {title} — {flag}\n"
        buttons.append(
            [InlineKeyboardButton(text=f"{'خاموش' if r.is_active else 'روشن'} کردن {title[:20]}", callback_data=f"adm:ct:{r.id}")]
        )
    buttons.append([InlineKeyboardButton(text="بازگشت", callback_data="adm:home")])
    await cb.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data == "adm:ca")
async def admin_channel_ask(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    await state.set_state(AdminSG.channel_ref)
    await cb.message.answer("آیدی عددی کانال یا @username را بفرستید. ربات باید ادمین کانال باشد.")
    await cb.answer()


@router.message(AdminSG.channel_ref)
async def admin_channel_add(message: Message, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ok(db, db_user):
        await _deny(message)
        return
    ref = (message.text or "").strip()
    if not ref:
        await message.answer("مقدار خالی است.")
        return
    try:
        await channel_svc.add_global_required_channel(db, message.bot, db_user.id, ref, scope="all")
        await message.answer("کانال اجباری اضافه شد.", reply_markup=_back_kb())
    except AppError as exc:
        await message.answer(exc.message)
    await state.clear()


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


@router.callback_query(F.data == "adm:rep")
async def admin_reports(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ok(db, db_user):
        await _deny(cb)
        return
    rows = (await db.scalars(select(Report).where(Report.status == "new").order_by(Report.created_at.desc()).limit(10))).all()
    if not rows:
        await cb.message.answer("گزارش جدیدی نیست.", reply_markup=_back_kb())
        await cb.answer()
        return
    for r in rows:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="بسته شد", callback_data=f"adm:rok:{r.id}")]]
        )
        await cb.message.answer(f"{r.reason}\n{r.body[:400]}", reply_markup=kb)
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
    row.status = "resolved"
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
            [InlineKeyboardButton(text="تأیید و ارسال", callback_data=f"adm:bok:{row.id}")],
            [InlineKeyboardButton(text="انصراف", callback_data="adm:home")],
        ]
    )
    await message.answer(f"پیش‌نویس آماده است:\n<b>{row.title}</b>\n{row.body}", reply_markup=kb)


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
    from app.workers.tasks import run_broadcast

    run_broadcast.delay(str(row.id))
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
