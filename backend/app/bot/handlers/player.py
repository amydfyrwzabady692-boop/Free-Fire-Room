from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.keyboards.common import (
    checklist_kb,
    event_detail_kb,
    event_list_kb,
    main_menu,
    membership_kb,
    tos_kb,
)
from app.core.config import get_settings
from app.core.enums import EventStatus, EventVisibility, RegistrationStatus
from app.core.rate_limit import hit_rate_limit
from app.core.time import format_local
from app.locales import fa as T
from app.models.channel import Channel
from app.models.event import Event
from app.models.registration import Registration
from app.models.user import User
from app.services.channels import active_global_channels
from app.services.referrals import apply_start_referral, get_or_create_link, validate_pending_referrals
from app.services.registration import register_user
from app.services.requirements import evaluate_requirements
from app.services.telegram_ops import get_membership

router = Router(name="player")


def _parse_start(payload: str | None) -> tuple[str, str | None]:
    if not payload:
        return "start", None
    if payload.startswith("event_"):
        return "event", payload[6:]
    if payload.startswith("ref_"):
        return "ref", payload[4:]
    if payload.startswith("org_"):
        return "org", payload[4:]
    return "start", payload


async def _ensure_onboarding(message: Message, user: User, db: AsyncSession) -> bool:
    if not user.tos_accepted_at:
        await message.answer(T.TOS, reply_markup=tos_kb())
        return False
    missing = await _missing_global_memberships(db, message.bot, user)
    if missing:
        buttons = []
        for ch, _ in missing:
            url = f"https://t.me/{ch.username}" if ch.username else ch.invite_link
            buttons.append((f"عضویت در {ch.title}", url or "https://t.me"))
        await message.answer(
            "برای استفاده از ربات باید در کانال‌های زیر عضو شوید، سپس «بررسی مجدد عضویت» را بزنید.",
            reply_markup=membership_kb(buttons),
        )
        return False
    if not user.onboarding_completed_at:
        user.onboarding_completed_at = datetime.now(UTC)
        await validate_pending_referrals(db, user)
        await db.flush()
    return True


async def _missing_global_memberships(db: AsyncSession, bot, user: User):
    rows = await active_global_channels(db, scope="player")
    missing = []
    for row in rows:
        ch = await db.get(Channel, row.channel_id)
        if not ch or not ch.bot_is_admin:
            continue
        result = await get_membership(bot, ch.telegram_chat_id, user.telegram_id)
        if not result.ok:
            missing.append((ch, result))
    return missing


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, db: AsyncSession, db_user: User):
    await hit_rate_limit(f"rl:start:{db_user.telegram_id}", get_settings().rate_limit_start_per_minute)
    kind, token = _parse_start(command.args)
    db_user.start_payload = command.args
    if kind == "ref" and token:
        await apply_start_referral(db, invitee=db_user, token=token)
    if kind == "event" and token:
        db_user.start_payload = f"event_{token}"
    await db.flush()
    if not await _ensure_onboarding(message, db_user, db):
        return
    extra = ""
    if kind == "event" and token:
        extra = "\nاز لینک اختصاصی یک کاستوم وارد شدید."
    await message.answer("منوی اصلی آماده است." + extra, reply_markup=main_menu())
    if kind == "event" and token:
        await _show_event(message, db, db_user, token)


@router.callback_query(F.data == "tos:accept")
async def tos_accept(cb: CallbackQuery, db: AsyncSession, db_user: User):
    db_user.tos_accepted_at = datetime.now(UTC)
    db_user.privacy_accepted_at = datetime.now(UTC)
    await db.flush()
    await cb.message.answer("شرایط پذیرفته شد.")
    if await _ensure_onboarding(cb.message, db_user, db):
        await cb.message.answer("خوش آمدید.", reply_markup=main_menu())
    await cb.answer()


@router.callback_query(F.data == "tos:privacy")
async def tos_privacy(cb: CallbackQuery):
    await cb.message.answer(T.PRIVACY)
    await cb.answer()


@router.callback_query(F.data == "membership:recheck")
async def membership_recheck(cb: CallbackQuery, db: AsyncSession, db_user: User):
    await hit_rate_limit(f"rl:mem:{db_user.telegram_id}", get_settings().rate_limit_membership_per_minute)
    if await _ensure_onboarding(cb.message, db_user, db):
        await cb.message.answer("عضویت تأیید شد.", reply_markup=main_menu())
    await cb.answer()


async def _list_events(db: AsyncSession, *, today: bool) -> list[Event]:
    now = datetime.now(UTC)
    stmt = (
        select(Event)
        .where(
            Event.deleted_at.is_(None),
            Event.visibility == EventVisibility.PUBLIC,
            Event.status.in_([EventStatus.PUBLISHED, EventStatus.FULL, EventStatus.STARTED]),
            Event.deep_link_active.is_(True),
        )
        .options(selectinload(Event.organizer), selectinload(Event.channel))
        .order_by(Event.starts_at.asc())
        .limit(20)
    )
    if today:
        end = now.replace(hour=23, minute=59, second=59)
        stmt = stmt.where(Event.starts_at >= now.replace(hour=0, minute=0, second=0), Event.starts_at <= end)
    else:
        stmt = stmt.where(Event.starts_at >= now)
    return list((await db.scalars(stmt)).all())


def _event_card(e: Event) -> str:
    org = e.organizer.display_name if e.organizer else "برگزارکننده"
    verified = " ✅" if e.organizer and e.organizer.verified_badge else ""
    ch = e.channel.title if e.channel else "-"
    left = max(0, int((e.starts_at - datetime.now(UTC)).total_seconds() // 60))
    return (
        f"<b>{e.title}</b>\n"
        f"برگزارکننده: {org}{verified}\n"
        f"کانال: {ch}\n"
        f"زمان: {format_local(e.starts_at, e.timezone)}\n"
        f"مانده تا شروع: {left} دقیقه\n"
        f"حالت: {e.game_mode} | سرور: {e.region}\n"
        f"ظرفیت: {e.confirmed_count}/{e.capacity}\n"
        f"جایزه: {e.prize_summary or '—'}\n"
        f"وضعیت: {e.status}"
    )


@router.message(F.text == "کاستوم‌های آینده")
@router.callback_query(F.data == "list:upcoming")
async def upcoming(event: Message | CallbackQuery, db: AsyncSession, db_user: User):
    msg = event.message if isinstance(event, CallbackQuery) else event
    if not await _ensure_onboarding(msg, db_user, db):
        return
    rows = await _list_events(db, today=False)
    if not rows:
        await msg.answer("کاستوم آینده‌ای یافت نشد.")
        return
    kb = event_list_kb([(e.public_token, e.title) for e in rows])
    await msg.answer("کاستوم‌های آینده:", reply_markup=kb)
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.message(F.text == "کاستوم‌های امروز")
async def today(message: Message, db: AsyncSession, db_user: User):
    if not await _ensure_onboarding(message, db_user, db):
        return
    rows = await _list_events(db, today=True)
    if not rows:
        await message.answer("برای امروز کاستومی نیست.")
        return
    await message.answer("کاستوم‌های امروز:", reply_markup=event_list_kb([(e.public_token, e.title) for e in rows]))


@router.callback_query(F.data.startswith("ev:"))
async def show_event_cb(cb: CallbackQuery, db: AsyncSession, db_user: User):
    token = cb.data.split(":", 1)[1]
    await _show_event(cb.message, db, db_user, token)
    await cb.answer()


async def _show_event(message: Message, db: AsyncSession, user: User, token: str):
    e = await db.scalar(
        select(Event)
        .where(Event.public_token == token)
        .options(selectinload(Event.organizer), selectinload(Event.channel), selectinload(Event.prizes))
    )
    if not e or not e.deep_link_active:
        await message.answer("این کاستوم در دسترس نیست یا لغو شده است.")
        return
    await message.answer(_event_card(e) + "\n\n" + T.DISCLAIMER, reply_markup=event_detail_kb(token))


@router.callback_query(F.data.startswith("join:"))
async def join_event(cb: CallbackQuery, db: AsyncSession, db_user: User):
    await hit_rate_limit(f"rl:reg:{db_user.telegram_id}", get_settings().rate_limit_register_per_minute)
    token = cb.data.split(":", 1)[1]
    e = await db.scalar(select(Event).where(Event.public_token == token))
    if not e:
        await cb.answer("یافت نشد", show_alert=True)
        return
    result = await register_user(db, user=db_user, event=e, bot=cb.bot, source="bot")
    if result.registration.status == RegistrationStatus.CONFIRMED:
        await cb.message.answer(
            f"ثبت‌نام شما قطعی شد.\nزمان ارسال مشخصات اتاق: {format_local(e.credentials_send_at, e.timezone)}\n"
            "اگر قبل از ارسال از کانال‌های اجباری خارج شوید، واجد شرایط نخواهید بود."
        )
    elif result.waitlisted:
        await cb.message.answer("ظرفیت پر است. شما در لیست انتظار قرار گرفتید.")
    else:
        text = "شرایط هنوز کامل نیست:\n"
        for item in result.checklist or []:
            mark = "✅" if item.status == "done" else "❌"
            text += f"{mark} {item.label}"
            if item.detail:
                text += f" — {item.detail}"
            text += "\n"
        await cb.message.answer(text, reply_markup=checklist_kb(token))
    await cb.answer()


@router.callback_query(F.data.startswith("req:"))
async def recheck_req(cb: CallbackQuery, db: AsyncSession, db_user: User):
    await hit_rate_limit(f"rl:mem:{db_user.telegram_id}", get_settings().rate_limit_membership_per_minute)
    token = cb.data.split(":", 1)[1]
    e = await db.scalar(select(Event).where(Event.public_token == token))
    if not e:
        await cb.answer("یافت نشد", show_alert=True)
        return
    reg = await db.scalar(
        select(Registration).where(Registration.event_id == e.id, Registration.user_id == db_user.id)
    )
    checklist = await evaluate_requirements(db, user=db_user, event=e, bot=cb.bot, registration=reg)
    text = "وضعیت شرایط:\n"
    for item in checklist.items:
        mark = "✅" if item.status == "done" else "⏳" if item.status == "pending_review" else "❌"
        text += f"{mark} {item.label}\n"
    await cb.message.answer(text, reply_markup=checklist_kb(token))
    if checklist.all_ok:
        await register_user(db, user=db_user, event=e, bot=cb.bot, source="recheck")
    await cb.answer()


@router.callback_query(F.data.startswith("rules:"))
async def accept_rules(cb: CallbackQuery, db: AsyncSession, db_user: User):
    token = cb.data.split(":", 1)[1]
    e = await db.scalar(select(Event).where(Event.public_token == token))
    if not e:
        await cb.answer("یافت نشد", show_alert=True)
        return
    result = await register_user(db, user=db_user, event=e, bot=cb.bot, source="rules", accept_rules=True)
    if result.registration.status == RegistrationStatus.CONFIRMED:
        await cb.message.answer("قوانین پذیرفته شد و ثبت‌نام قطعی شد.")
    else:
        await cb.message.answer("قوانین پذیرفته شد. سایر شرایط را کامل کنید.")
    await cb.answer()


@router.callback_query(F.data.startswith("inv:"))
async def invite(cb: CallbackQuery, db: AsyncSession, db_user: User):
    await hit_rate_limit(f"rl:ref:{db_user.telegram_id}", get_settings().rate_limit_referral_per_minute)
    token = cb.data.split(":", 1)[1]
    e = await db.scalar(select(Event).where(Event.public_token == token))
    link = await get_or_create_link(db, db_user.id, e.id if e else None)
    bot_user = get_settings().bot_username
    url = f"https://t.me/{bot_user}?start=ref_{link.token}"
    await cb.message.answer(
        f"لینک دعوت اختصاصی شما:\n{url}\n\n"
        f"دعوت‌های معتبر: {link.valid_count}\n"
        "دعوت وقتی معتبر است که فرد جدید ربات را استارت کند و عضویت اجباری را کامل کند.\n"
        "فوروارد بنر به‌تنهایی قابل اثبات نیست و تأیید قطعی محسوب نمی‌شود."
    )
    await cb.answer()


@router.message(F.text == "دعوت دوستان")
async def invite_global(message: Message, db: AsyncSession, db_user: User):
    if not await _ensure_onboarding(message, db_user, db):
        return
    link = await get_or_create_link(db, db_user.id, None, campaign="global")
    url = f"https://t.me/{get_settings().bot_username}?start=ref_{link.token}"
    await message.answer(f"لینک دعوت شما:\n{url}\nدعوت‌های معتبر: {link.valid_count}")


@router.message(F.text == "ثبت‌نام‌های من")
async def my_regs(message: Message, db: AsyncSession, db_user: User):
    rows = (
        await db.scalars(
            select(Registration)
            .where(Registration.user_id == db_user.id)
            .options(selectinload(Registration.event))
            .order_by(Registration.created_at.desc())
            .limit(20)
        )
    ).all()
    if not rows:
        await message.answer("ثبت‌نامی ندارید.")
        return
    text = "ثبت‌نام‌های شما:\n"
    for r in rows:
        text += f"• {r.event.title} — {r.status}\n"
    await message.answer(text)


@router.message(F.text == "نتایج و تاریخچه")
async def history(message: Message, db: AsyncSession, db_user: User):
    rows = (
        await db.scalars(
            select(Event)
            .join(Registration, Registration.event_id == Event.id)
            .where(Registration.user_id == db_user.id, Event.status.in_([EventStatus.FINISHED, EventStatus.CANCELLED]))
            .limit(20)
        )
    ).all()
    if not rows:
        await message.answer("تاریخچه‌ای موجود نیست.")
        return
    await message.answer("\n".join(f"• {e.title} ({e.status})" for e in rows))


@router.message(F.text == "راهنما و قوانین")
async def help_msg(message: Message):
    await message.answer(T.HELP + "\n\n" + T.DISCLAIMER)


@router.message(F.text == "پشتیبانی")
async def support(message: Message):
    await message.answer("پیام خود را برای پشتیبانی بنویسید. تیم مدیریت آن را در پنل می‌بیند.\n" + T.DISCLAIMER)


@router.message(F.text == "پروفایل")
async def profile(message: Message, db_user: User):
    ff = db_user.profile.ff_player_id if db_user.profile else "—"
    await message.answer(
        f"شناسه تلگرام: {db_user.telegram_id}\n"
        f"نام: {db_user.first_name}\n"
        f"Free Fire ID: {ff}\n"
        f"منطقه زمانی: {db_user.timezone}\n"
        "برای تنظیم شناسه بازیکن بنویسید:\n/setid شناسه"
    )


@router.message(F.text.startswith("/setid"))
async def set_id(message: Message, db_user: User, db: AsyncSession):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("مثال: /setid 123456789")
        return
    if db_user.profile:
        db_user.profile.ff_player_id = parts[1].strip()[:32]
    await message.answer("شناسه Free Fire ذخیره شد.")


@router.message(F.text == "اعلان‌های من")
async def notifs(message: Message, db: AsyncSession, db_user: User):
    from app.models.jobs import Notification

    rows = (
        await db.scalars(
            select(Notification)
            .where(Notification.user_id == db_user.id)
            .order_by(Notification.created_at.desc())
            .limit(10)
        )
    ).all()
    if not rows:
        await message.answer("اعلانی ندارید.")
        return
    await message.answer("\n\n".join(f"<b>{n.title}</b>\n{n.body}" for n in rows))


@router.callback_query(F.data.startswith("reveal:"))
async def reveal(cb: CallbackQuery):
    await cb.answer(
        "اطلاعات اتاق فقط در پیام خصوصی ارسال می‌شود. اگر پیام را دریافت نکرده‌اید واجد شرایط نبوده‌اید یا هنوز زمان ارسال نرسیده است.",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("rep:"))
async def report_event(cb: CallbackQuery, db: AsyncSession, db_user: User):
    from app.models.report import Report

    token = cb.data.split(":", 1)[1]
    e = await db.scalar(select(Event).where(Event.public_token == token))
    db.add(
        Report(
            reporter_id=db_user.id,
            event_id=e.id if e else None,
            organizer_id=e.organizer_id if e else None,
            reason="other",
            body="گزارش از ربات — جزئیات را در پیام بعدی بفرستید یا از پنل پیگیری کنید.",
            status="new",
        )
    )
    await cb.message.answer("گزارش ثبت شد. اگر مدرک دارید برای پشتیبانی ارسال کنید.")
    await cb.answer()


@router.callback_query(F.data == "menu:home")
async def menu_home(cb: CallbackQuery):
    await cb.message.answer("منوی اصلی", reply_markup=main_menu())
    await cb.answer()
