from __future__ import annotations

from datetime import UTC, date, datetime as dt
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.access import menu_for
from app.bot.helpers import event_deep_link
from app.bot.keyboards.common import DANGER, PRIMARY, SUCCESS, ibtn, organizer_home_kb, pick_date_kb, wizard_nav
from app.bot.onboarding import ensure_onboarding, target_message
from app.bot.states.groups import CredsWaitSG, EventWizardSG
from app.core.config import get_settings
from app.core.enums import BanScope, EventStatus, OrganizerStatus
from app.core.errors import AppError
from app.core.time import combine_local_date_and_clock, format_jalali_date, format_local, parse_clock, upcoming_local_dates
from app.models.channel import ChannelOwnership
from app.models.event import Event, RoomCredential
from app.models.organizer import Organizer
from app.models.user import User
from app.services.bans import is_banned
from app.services.channels import connect_organizer_channel
from app.services.events import cancel_event, create_event, submit_for_publish, update_credentials, waiting_live_credential_event
from app.services.organizers import get_or_apply
from app.services.reports import credentials_deadline, credentials_window_open, creds_were_provided
from app.services.settings import get_setting

router = Router(name="organizer")

DEFAULT_RULES = (
    "برای دریافت شناسه و رمز اتاق باید تا لحظه ارسال در کانال‌های اجباری عضو بمانید "
    "و شرایط کاستوم را کامل کرده باشید. خروج از کانال قبل از ارسال یعنی عدم ارسال مشخصات."
)


async def _blocked_organize(db: AsyncSession, user: User, target: Message | CallbackQuery) -> bool:
    ban = await is_banned(db, user, BanScope.ORGANIZE)
    if not ban:
        return False
    text = "امکان ثبت کاستوم برای شما محدود شده است."
    if isinstance(target, CallbackQuery):
        await target.answer(text, show_alert=True)
    else:
        await target.answer(text)
    return True


@router.message(F.text.in_({"ثبت کاستوم", "ثبت کاستوم جایزه‌دار", "ثبت اطلاع‌رسانی"}))
@router.callback_query(F.data == "orgp:new")
async def start_org(event: Message | CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    msg = target_message(event)
    if not await ensure_onboarding(msg, db_user, db):
        if isinstance(event, CallbackQuery):
            await event.answer()
        return
    if await _blocked_organize(db, db_user, event):
        return
    org = await get_or_apply(db, db_user, db_user.first_name)
    if org.status == OrganizerStatus.PENDING:
        await msg.answer("درخواست برگزارکننده شما ثبت شد و منتظر تأیید مدیریت است.")
        if isinstance(event, CallbackQuery):
            await event.answer()
        return
    if org.status in {OrganizerStatus.REJECTED, OrganizerStatus.SUSPENDED}:
        await msg.answer("حساب برگزارکننده شما فعال نیست. با پشتیبانی تماس بگیرید.")
        if isinstance(event, CallbackQuery):
            await event.answer()
        return
    await state.set_state(EventWizardSG.starts_at)
    await state.update_data(required_channel_ids=[])
    await msg.answer(
        "روز کاستوم را انتخاب کنید (شمسی، تهران).\n"
        "بعد ساعت را می‌فرستید.",
        reply_markup=pick_date_kb("wzd"),
    )
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.message(F.text.in_({"پنل برگزارکننده", "کاستوم‌های من", "پنل برگزار کننده"}))
@router.callback_query(F.data == "orgp:home")
async def org_home(event: Message | CallbackQuery, db: AsyncSession, db_user: User):
    msg = target_message(event)
    if not await ensure_onboarding(msg, db_user, db):
        if isinstance(event, CallbackQuery):
            await event.answer()
        return
    if await _blocked_organize(db, db_user, event):
        return
    await get_or_apply(db, db_user, db_user.first_name)
    from app.services.reviews import format_rating_line, review_summary_for_organizer

    org = await db.scalar(select(Organizer).where(Organizer.user_id == db_user.id))
    rating = ""
    if org:
        rating = "\n" + format_rating_line(await review_summary_for_organizer(db, org.id), prefix="امتیاز شما از بازیکن‌ها")
    await msg.answer(
        "پنل برگزارکننده — این پنل مالک ربات نیست.\n\n"
        "اینجا کاستوم جایزه‌دار خودتان را می‌گذارید: ساعت + کانال جوین اجباری. "
        "در فهرست همه دیده می‌شود. سر ساعت آیدی و رمز را داخل ربات می‌فرستید.\n"
        "آمار عضو و اینکه چند نفر شرایط را کامل کرده‌اند را هم همین‌جا می‌بینید."
        f"{rating}",
        reply_markup=organizer_home_kb(),
    )
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.message(F.text == "/cancel")
@router.callback_query(F.data == "wiz:cancel")
async def cancel_wiz(event: Message | CallbackQuery, state: FSMContext, db: AsyncSession, db_user: User):
    await state.clear()
    msg = event.message if isinstance(event, CallbackQuery) else event
    await msg.answer("لغو شد.", reply_markup=await menu_for(db, db_user))
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.callback_query(EventWizardSG.starts_at, F.data.startswith("wzd:"))
async def wiz_pick_date(cb: CallbackQuery, state: FSMContext):
    try:
        offset = int(cb.data.split(":")[1])
    except (IndexError, ValueError):
        await cb.answer("نامعتبر", show_alert=True)
        return
    choices = upcoming_local_dates(3)
    if offset < 0 or offset >= len(choices):
        await cb.answer("این روز در دسترس نیست.", show_alert=True)
        return
    day = choices[offset]["date"]
    await state.update_data(picked_date=day.isoformat())
    await state.set_state(EventWizardSG.starts_time)
    await cb.message.answer(
        f"تاریخ: {format_jalali_date(day)}\n"
        "حالا ساعت را بفرستید. نمونه: 22:00 یا 22\n"
        "سر همین ساعت آیدی و رمز فقط برای کسانی می‌رود که کانال‌ها را جوین کرده باشند.",
        reply_markup=wizard_nav(),
    )
    await cb.answer()


@router.message(EventWizardSG.starts_at)
async def wiz_need_date(message: Message):
    await message.answer("یکی از دکمه‌های امروز / فردا / پس‌فردا را بزنید.", reply_markup=pick_date_kb("wzd"))


@router.message(EventWizardSG.starts_time)
async def wiz_starts(message: Message, state: FSMContext):
    data = await state.get_data()
    picked = data.get("picked_date")
    if not picked:
        await state.set_state(EventWizardSG.starts_at)
        await message.answer("اول روز را انتخاب کنید.", reply_markup=pick_date_kb("wzd"))
        return
    try:
        hour, minute = parse_clock(message.text or "")
        when = combine_local_date_and_clock(date.fromisoformat(picked), hour, minute)
    except ValueError:
        await message.answer("ساعت نامعتبر است. نمونه: 22:00 یا 22")
        return
    if when <= dt.now(UTC):
        await message.answer("این ساعت گذشته است. ساعت بعدی همین روز را بفرستید.")
        return
    iso = when.isoformat()
    await state.update_data(
        starts_at=iso,
        registration_ends_at=iso,
        credentials_send_at=iso,
        required_channel_ids=[],
    )
    await state.set_state(EventWizardSG.channel)
    await message.answer(
        f"زمان کاستوم: {format_local(when)}\n\n"
        "اولین کانال جوین اجباری را بفرستید: @username یا آیدی عددی.\n\n"
        "ربات را اول ادمین آن کانال کنید. بدون ادمین بودن ربات، عضویت بازیکن قابل بررسی نیست و رمز نباید بی‌دلیل ارسال شود."
    )


@router.message(EventWizardSG.channel)
async def wiz_channel(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    ref = (message.text or "").strip()
    try:
        ch = await connect_organizer_channel(
            db, message.bot, db_user, int(ref) if ref.lstrip("-").isdigit() else ref
        )
    except Exception as exc:  # noqa: BLE001
        await message.answer(str(getattr(exc, "message", exc)))
        return
    await state.update_data(
        channel_id=str(ch.id),
        required_channel_ids=[str(ch.id)],
        title=f"کاستوم {ch.title}"[:160],
        channel_title=ch.title,
    )
    await state.set_state(EventWizardSG.extra_channels)
    await message.answer(
        f"کانال «{ch.title}» به‌عنوان جوین اجباری ثبت شد.\n"
        "اگر کانال اجباری دیگری هم می‌خواهید @username را بفرستید.\n"
        "اگر تمام شد «-» بفرستید."
    )


@router.message(EventWizardSG.extra_channels)
async def wiz_extra(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    text = (message.text or "").strip()
    data = await state.get_data()
    ids: list[str] = list(data.get("required_channel_ids") or [])
    max_ch = int(await get_setting(db, "max_required_channels_per_event", 5))
    if text != "-":
        if len(ids) >= max_ch:
            await message.answer(f"سقف کانال اجباری {max_ch} است. «-» بفرستید.")
            return
        try:
            ch = await connect_organizer_channel(
                db, message.bot, db_user, int(text) if text.lstrip("-").isdigit() else text
            )
        except Exception as exc:  # noqa: BLE001
            await message.answer(str(getattr(exc, "message", exc)))
            return
        if str(ch.id) not in ids:
            ids.append(str(ch.id))
            await state.update_data(required_channel_ids=ids)
        await message.answer(
            f"کانال «{ch.title}» اضافه شد ({len(ids)}/{max_ch}).\nکانال جوین اجباری بعدی یا «-» برای ادامه."
        )
        return
    await _publish_custom(message, state, db, db_user)


async def _publish_custom(message: Message, state: FSMContext, db: AsyncSession, db_user: User) -> None:
    data = await state.get_data()
    org = await db.scalar(select(Organizer).where(Organizer.user_id == db_user.id))
    payload = {
        "title": data.get("title") or "کاستوم جایزه‌دار",
        "starts_at": dt.fromisoformat(data["starts_at"]),
        "registration_ends_at": dt.fromisoformat(data["registration_ends_at"]),
        "credentials_send_at": dt.fromisoformat(data["credentials_send_at"]),
        "channel_id": UUID(data["channel_id"]),
        "required_channel_ids": [UUID(x) for x in data.get("required_channel_ids") or []],
        "capacity": 100,
        "region": "ME",
        "game_mode": "squad",
        "prize_summary": data.get("channel_title") or "کاستوم جایزه‌دار",
        "prizes": [{"place": 1, "title": "کاستوم جایزه‌دار"}],
        "rules_text": DEFAULT_RULES,
        "require_rules_accept": False,
        "required_referrals": 0,
        "waitlist_enabled": True,
        "visibility": "public",
    }
    try:
        event = await create_event(db, org, payload, db_user.id)
        await submit_for_publish(db, event, db_user.id)
        link = event_deep_link(event.public_token)
        if event.status == EventStatus.PUBLISHED:
            await message.answer(
                "کاستوم در فهرست همه کاربران قرار گرفت.\n\n"
                f"ساعت کاستوم (شمسی): {format_local(event.starts_at, event.timezone)}\n"
                f"کانال جوین اجباری: {len(payload['required_channel_ids'])} مورد\n\n"
                f"<b>لینک این کاستوم:</b>\n{link}\n\n"
                "سر همین ساعت ربات از شما آیدی و رمز را می‌گیرد. "
                "بعد فقط برای کسانی که کانال‌ها را جوین کرده‌اند ارسال می‌شود.",
                reply_markup=await menu_for(db, db_user),
            )
        else:
            await message.answer(
                "کاستوم ثبت شد و منتظر تأیید مدیر است.\n"
                f"پس از تأیید در فهرست می‌آید:\n{link}",
                reply_markup=await menu_for(db, db_user),
            )
    except AppError as exc:
        await message.answer(exc.message)
        return
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"خطا: {getattr(exc, 'message', exc)}")
        return
    await state.clear()


@router.callback_query(F.data == "orgp:mine")
async def org_mine(cb: CallbackQuery, db: AsyncSession, db_user: User):
    org = await db.scalar(select(Organizer).where(Organizer.user_id == db_user.id))
    if not org:
        await cb.answer("اول یک کاستوم بسازید.", show_alert=True)
        return
    rows = (
        await db.scalars(
            select(Event)
            .where(Event.organizer_id == org.id, Event.deleted_at.is_(None))
            .order_by(Event.starts_at.desc())
            .limit(15)
        )
    ).all()
    if not rows:
        await cb.message.answer("هنوز کاستومی ندارید.", reply_markup=organizer_home_kb())
        await cb.answer()
        return
    from app.services.reviews import (
        event_audience_stats,
        format_audience_stats,
        format_rating_line,
        review_summary_for_event,
    )

    for e in rows:
        stats = await event_audience_stats(db, e.id)
        rating = format_rating_line(await review_summary_for_event(db, e.id), prefix="امتیاز این کاستوم")
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [ibtn("ارسال آیدی و رمز", callback_data=f"orgp:creds:{e.public_token}", style=SUCCESS)],
                [ibtn("لینک اختصاصی", callback_data=f"orgp:link:{e.public_token}", style=PRIMARY)],
                [ibtn("لغو کاستوم", callback_data=f"orgp:cancel:{e.public_token}", style=DANGER)],
            ]
        )
        await cb.message.answer(
            f"<b>{e.title}</b>\n"
            f"زمان (شمسی): {format_local(e.starts_at, e.timezone)}\n"
            f"وضعیت: {e.status}\n"
            f"{format_audience_stats(stats)}\n"
            f"{rating}",
            reply_markup=kb,
        )
    await cb.message.answer("بازگشت به پنل:", reply_markup=organizer_home_kb())
    await cb.answer()


@router.callback_query(F.data.startswith("orgp:link:"))
async def org_link(cb: CallbackQuery, db: AsyncSession, db_user: User):
    token = cb.data.split(":", 2)[-1]
    e = await db.scalar(select(Event).where(Event.public_token == token).options(selectinload(Event.organizer)))
    if not e or not e.organizer or e.organizer.user_id != db_user.id:
        await cb.answer("یافت نشد", show_alert=True)
        return
    link = event_deep_link(e.public_token)
    await cb.message.answer(
        f"لینک اختصاصی «{e.title}»:\n{link}\n\n"
        "این لینک را در کانال بگذارید. مشخصات اتاق فقط به کسانی می‌رسد که شرایط را تا لحظه ارسال کامل کرده باشند."
    )
    await cb.answer()


@router.callback_query(F.data.startswith("orgp:cancel:"))
async def org_cancel(cb: CallbackQuery, db: AsyncSession, db_user: User):
    token = cb.data.split(":", 2)[-1]
    e = await db.scalar(select(Event).where(Event.public_token == token).options(selectinload(Event.organizer)))
    if not e or not e.organizer or e.organizer.user_id != db_user.id:
        await cb.answer("یافت نشد", show_alert=True)
        return
    if e.status in {EventStatus.CANCELLED, EventStatus.FINISHED}:
        await cb.answer("این کاستوم قابل لغو نیست.", show_alert=True)
        return
    await cancel_event(db, e, db_user.id, "لغو توسط برگزارکننده")
    await cb.message.answer(f"کاستوم «{e.title}» لغو شد.")
    await cb.answer()


@router.callback_query(F.data == "orgp:ch")
async def org_channels(cb: CallbackQuery, db: AsyncSession, db_user: User):
    rows = (
        await db.scalars(
            select(ChannelOwnership)
            .where(ChannelOwnership.user_id == db_user.id, ChannelOwnership.is_active.is_(True))
            .options(selectinload(ChannelOwnership.channel))
        )
    ).all()
    if not rows:
        await cb.message.answer(
            "کانالی وصل نشده. هنگام ثبت کاستوم کانال را اضافه کنید.",
            reply_markup=organizer_home_kb(),
        )
        await cb.answer()
        return
    text = "کانال‌های تأییدشده شما:\n"
    for own in rows:
        ch = own.channel
        if not ch:
            continue
        admin = "ادمین ربات: بله" if ch.bot_is_admin else "ادمین ربات: خیر — عضویت قابل بررسی نیست"
        text += f"• {ch.title} (@{ch.username or '-'}) — {admin}\n"
    await cb.message.answer(text, reply_markup=organizer_home_kb())
    await cb.answer()


@router.callback_query(F.data.startswith("orgp:creds:"))
async def ask_live_creds(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    token = cb.data.split(":", 2)[-1]
    e = await db.scalar(select(Event).where(Event.public_token == token).options(selectinload(Event.organizer)))
    if not e or not e.organizer or e.organizer.user_id != db_user.id:
        await cb.answer("یافت نشد", show_alert=True)
        return
    creds = await db.scalar(select(RoomCredential).where(RoomCredential.event_id == e.id))
    if not credentials_window_open(e) and not creds_were_provided(creds):
        await cb.answer("فرصت ۵ دقیقه‌ای تمام شد.", show_alert=True)
        return
    await state.set_state(CredsWaitSG.waiting)
    await state.update_data(event_token=token)
    grace = get_settings().credentials_grace_minutes
    deadline = credentials_deadline(e)
    remain = max(0, int((deadline - dt.now(UTC)).total_seconds() // 60))
    await cb.message.answer(
        f"ساعت کاستوم «{e.title}»\n"
        "الان Room ID و Password را در یک خط بفرستید؛ مثال:\n"
        "<code>12345678 mypass</code>\n\n"
        f"فقط {grace} دقیقه بعد از ساعت شروع فرصت دارید (حدود {remain} دقیقه مانده).\n"
        "اگر نفرستید اخطار می‌گیرید و بازیکن‌ها می‌توانند گزارش بدهند.\n"
        "بعد فقط برای کسانی ارسال می‌شود که کانال‌های این کاستوم را جوین کرده باشند.",
    )
    await cb.answer()


def _looks_like_room_creds(text: str | None) -> tuple[str, str] | None:
    parts = (text or "").split(maxsplit=1)
    if len(parts) != 2:
        return None
    room_id, password = parts[0].strip(), parts[1].strip()
    if not room_id.isdigit() or not (4 <= len(room_id) <= 16) or len(password) < 1:
        return None
    return room_id, password


async def _save_and_dispatch_creds(
    message: Message,
    db: AsyncSession,
    db_user: User,
    event: Event,
    room_id: str,
    password: str,
) -> bool:
    creds = await db.scalar(select(RoomCredential).where(RoomCredential.event_id == event.id))
    if not credentials_window_open(event) and not creds_were_provided(creds):
        await message.answer("فرصت ۵ دقیقه‌ای تمام شد. دیگر نمی‌توانید آیدی و رمز بفرستید.")
        return False
    await update_credentials(db, event, db_user.id, room_id, password)
    await db.commit()
    now = dt.now(UTC)
    if now < event.credentials_send_at:
        await message.answer(
            "ذخیره شد. سر ساعت برای کسانی که جوین کرده‌اند ارسال می‌شود.",
            reply_markup=await menu_for(db, db_user),
        )
        return True
    from app.workers.tasks import send_event_credentials

    send_event_credentials.delay(str(event.id))
    await message.answer(
        "گرفته شد. در حال ارسال آیدی و رمز برای کاربرانی که شرایط جوین را انجام داده‌اند.",
        reply_markup=await menu_for(db, db_user),
    )
    return True


@router.message(CredsWaitSG.waiting)
async def receive_live_creds(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    parsed = _looks_like_room_creds(message.text)
    if parsed is None:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("هر دو مقدار لازم است. نمونه:\n12345678 mypass")
            return
        parsed = (parts[0], parts[1])
    data = await state.get_data()
    token = data.get("event_token")
    e = await db.scalar(select(Event).where(Event.public_token == token).options(selectinload(Event.organizer)))
    if not e or not e.organizer or e.organizer.user_id != db_user.id:
        await state.clear()
        await message.answer("کاستوم یافت نشد.")
        return
    ok = await _save_and_dispatch_creds(message, db, db_user, e, parsed[0], parsed[1])
    if ok:
        await state.clear()


@router.message(StateFilter(default_state), F.text.regexp(r"^\d{4,16}\s+\S+"))
async def maybe_live_creds(message: Message, db: AsyncSession, db_user: User):
    parsed = _looks_like_room_creds(message.text)
    if parsed is None:
        return
    e = await waiting_live_credential_event(db, db_user.id)
    if not e:
        return
    await _save_and_dispatch_creds(message, db, db_user, e, parsed[0], parsed[1])

