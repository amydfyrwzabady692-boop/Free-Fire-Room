from __future__ import annotations

from datetime import UTC, date, datetime as dt, timedelta
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardMarkup, Message
from aiogram.enums import ChatMemberStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.access import menu_for
from app.bot.helpers import event_deep_link, extract_channel_ref
from app.bot.keyboards.common import (
    DANGER,
    PRIMARY,
    SUCCESS,
    add_required_channel_kb,
    ibtn,
    organizer_home_kb,
    pick_date_kb,
    wizard_nav,
)
from app.bot.onboarding import ensure_onboarding, target_message
from app.bot.states.groups import CredsWaitSG, EventWizardSG
from app.core.config import get_settings
from app.core.enums import BanScope, EventStatus, OrganizerStatus, RegistrationStatus
from app.core.errors import AppError
from app.core.time import combine_local_date_and_clock, format_jalali_date, format_local, parse_clock, upcoming_local_dates
from app.models.channel import Channel, ChannelOwnership
from app.models.event import Event, RoomCredential
from app.models.organizer import Organizer
from app.models.registration import Registration
from app.models.user import User
from app.services.bans import is_banned
from app.services.channels import connect_organizer_channel, list_owned_channels
from app.services.events import MIN_START_LEAD_MINUTES, cancel_event, create_event, submit_for_publish, update_credentials, waiting_live_credential_event
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


@router.message(F.text.in_({"ثبت کاستوم", "ثبت کاستوم جایزه‌دار"}))
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
async def wiz_starts(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
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
    if when <= dt.now(UTC) + timedelta(minutes=MIN_START_LEAD_MINUTES):
        await message.answer(
            f"ساعت باید حداقل {MIN_START_LEAD_MINUTES} دقیقه بعد باشد تا بازیکن‌ها وقت جوین داشته باشند."
        )
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
        f"زمان کاستوم: {format_local(when)}\n\n" + CHANNEL_STEP_TEXT,
        reply_markup=await _channel_step_kb(db, db_user, [], extra=False),
    )


CHANNEL_STEP_TEXT = (
    "کانال جوین اجباری را وصل کنید — دیگر لازم نیست آیدی عددی حفظ کنید.\n\n"
    "آسان‌ترین راه:\n"
    "۱) دکمه «افزودن ربات به کانال» را بزنید و کانال را انتخاب کنید تا ربات ادمین شود.\n"
    "۲) بعد همین‌جا یک پست از آن کانال را فوروارد کنید، یا @username / لینک را بفرستید.\n\n"
    "اگر قبلاً کانالی وصل کرده‌اید، از دکمه‌های پایین انتخابش کنید."
)


async def _owned_channel_buttons(db: AsyncSession, db_user: User, used_ids: list[str]) -> list[tuple[str, str]]:
    owned = await list_owned_channels(db, db_user.id)
    items = []
    used = set(used_ids)
    for ch in owned:
        if str(ch.id) in used:
            continue
        items.append((str(ch.id), ch.title or str(ch.telegram_chat_id)))
    return items[:8]


async def _channel_step_kb(db: AsyncSession, db_user: User, used_ids: list[str], *, extra: bool) -> InlineKeyboardMarkup:
    return add_required_channel_kb(
        await _owned_channel_buttons(db, db_user, used_ids),
        include_done=extra and bool(used_ids),
    )


def _private_fsm(bot, user_telegram_id: int) -> FSMContext:
    from aiogram.fsm.storage.base import StorageKey

    from app.bot.loader import get_dispatcher

    dp = get_dispatcher()
    return FSMContext(
        storage=dp.storage,
        key=StorageKey(bot_id=bot.id, chat_id=user_telegram_id, user_id=user_telegram_id),
    )


async def _attach_wizard_channel(
    *,
    bot,
    telegram_id: int,
    state: FSMContext,
    db: AsyncSession,
    db_user: User,
    ch,
    extra: bool,
) -> None:
    data = await state.get_data()
    ids: list[str] = list(data.get("required_channel_ids") or [])
    max_ch = int(await get_setting(db, "max_required_channels_per_event", 5))
    if extra and len(ids) >= max_ch and str(ch.id) not in ids:
        await bot.send_message(telegram_id, f"سقف کانال اجباری {max_ch} است. «تمام شد» را بزنید.")
        return
    if str(ch.id) not in ids:
        ids.append(str(ch.id))
    payload = {"required_channel_ids": ids, "channel_title": ch.title}
    if not data.get("channel_id"):
        payload["channel_id"] = str(ch.id)
        payload["title"] = f"کاستوم {ch.title}"[:160]
    await state.update_data(**payload)
    max_ch = int(await get_setting(db, "max_required_channels_per_event", 5))
    if not extra:
        await state.set_state(EventWizardSG.extra_channels)
        await bot.send_message(
            telegram_id,
            f"کانال «{ch.title}» به‌عنوان جوین اجباری ثبت شد.\n"
            "اگر کانال دیگری هم می‌خواهید همان روش را تکرار کنید.\n"
            "اگر تمام شد دکمه «تمام شد» را بزنید یا «-» بفرستید.",
            reply_markup=await _channel_step_kb(db, db_user, ids, extra=True),
        )
        return
    await bot.send_message(
        telegram_id,
        f"کانال «{ch.title}» اضافه شد ({len(ids)}/{max_ch}).\n"
        "کانال بعدی، یا «تمام شد» / «-».",
        reply_markup=await _channel_step_kb(db, db_user, ids, extra=True),
    )


@router.message(EventWizardSG.channel)
async def wiz_channel(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    ref = extract_channel_ref(message)
    if ref is None:
        await message.answer(
            "کانال شناخته نشد.\nدکمه افزودن ربات را بزنید، یا یک پست از کانال را فوروارد کنید.",
            reply_markup=await _channel_step_kb(db, db_user, [], extra=False),
        )
        return
    try:
        ch = await connect_organizer_channel(db, message.bot, db_user, ref)
    except Exception as exc:  # noqa: BLE001
        await message.answer(
            str(getattr(exc, "message", exc)) + "\nاگر ربات هنوز ادمین نیست، اول دکمه «افزودن ربات به کانال» را بزنید.",
            reply_markup=await _channel_step_kb(db, db_user, [], extra=False),
        )
        return
    await _attach_wizard_channel(
        bot=message.bot,
        telegram_id=message.chat.id,
        state=state,
        db=db,
        db_user=db_user,
        ch=ch,
        extra=False,
    )


@router.message(EventWizardSG.extra_channels)
async def wiz_extra(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    text = (message.text or "").strip()
    data = await state.get_data()
    ids: list[str] = list(data.get("required_channel_ids") or [])
    if text in {"-", "تمام شد", "ادامه"}:
        if not ids:
            await message.answer("حداقل یک کانال لازم است.", reply_markup=await _channel_step_kb(db, db_user, [], extra=False))
            return
        await _publish_custom(message, state, db, db_user)
        return
    max_ch = int(await get_setting(db, "max_required_channels_per_event", 5))
    if len(ids) >= max_ch:
        await message.answer(f"سقف کانال اجباری {max_ch} است. «تمام شد» را بزنید.")
        return
    ref = extract_channel_ref(message)
    if ref is None:
        await message.answer(
            "کانال شناخته نشد. فوروارد پست، @username، یا دکمه افزودن ربات.",
            reply_markup=await _channel_step_kb(db, db_user, ids, extra=True),
        )
        return
    try:
        ch = await connect_organizer_channel(db, message.bot, db_user, ref)
    except Exception as exc:  # noqa: BLE001
        await message.answer(
            str(getattr(exc, "message", exc)),
            reply_markup=await _channel_step_kb(db, db_user, ids, extra=True),
        )
        return
    await _attach_wizard_channel(
        bot=message.bot,
        telegram_id=message.chat.id,
        state=state,
        db=db,
        db_user=db_user,
        ch=ch,
        extra=True,
    )


@router.callback_query(F.data == "chdone")
async def wiz_channels_done(cb: CallbackQuery, state: FSMContext, db: AsyncSession, db_user: User):
    current = await state.get_state()
    if current not in {EventWizardSG.extra_channels.state, EventWizardSG.channel.state}:
        await cb.answer("الان در ثبت کاستوم نیستید.", show_alert=True)
        return
    data = await state.get_data()
    if not data.get("required_channel_ids"):
        await cb.answer("حداقل یک کانال لازم است.", show_alert=True)
        return
    await _publish_custom(cb.message, state, db, db_user)
    await cb.answer()


@router.callback_query(F.data.startswith("chpick:"))
async def wiz_pick_owned_channel(cb: CallbackQuery, state: FSMContext, db: AsyncSession, db_user: User):
    current = await state.get_state()
    extra = current == EventWizardSG.extra_channels.state
    if current not in {EventWizardSG.channel.state, EventWizardSG.extra_channels.state}:
        await cb.answer("اول ثبت کاستوم را شروع کنید.", show_alert=True)
        return
    try:
        ch = await db.get(Channel, UUID(cb.data.split(":", 1)[1]))
    except ValueError:
        await cb.answer("نامعتبر", show_alert=True)
        return
    if not ch:
        await cb.answer("کانال یافت نشد", show_alert=True)
        return
    try:
        ch = await connect_organizer_channel(db, cb.bot, db_user, ch.telegram_chat_id)
    except Exception as exc:  # noqa: BLE001
        await cb.answer(str(getattr(exc, "message", exc)), show_alert=True)
        return
    await _attach_wizard_channel(
        bot=cb.bot,
        telegram_id=cb.from_user.id,
        state=state,
        db=db,
        db_user=db_user,
        ch=ch,
        extra=extra,
    )
    await cb.answer("ثبت شد")


@router.my_chat_member()
async def bot_added_as_channel_admin(event: ChatMemberUpdated, db: AsyncSession, db_user: User | None = None):
    if not db_user:
        return
    new = event.new_chat_member
    if not new.user.is_bot or new.status not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}:
        return
    if event.chat.type not in {"channel", "supergroup"}:
        return
    me = await event.bot.get_me()
    if new.user.id != me.id:
        return
    try:
        ch = await connect_organizer_channel(db, event.bot, db_user, event.chat.id)
    except Exception as exc:  # noqa: BLE001
        try:
            await event.bot.send_message(
                event.from_user.id,
                f"ربات به کانال «{event.chat.title}» اضافه شد، ولی ثبت نشد:\n{getattr(exc, 'message', exc)}",
            )
        except Exception:
            return
        return
    state = _private_fsm(event.bot, event.from_user.id)
    current = await state.get_state()
    extra = current == EventWizardSG.extra_channels.state
    if current in {EventWizardSG.channel.state, EventWizardSG.extra_channels.state}:
        await _attach_wizard_channel(
            bot=event.bot,
            telegram_id=event.from_user.id,
            state=state,
            db=db,
            db_user=db_user,
            ch=ch,
            extra=extra,
        )
        return
    from app.bot.states.groups import AdminSG

    if current == AdminSG.channel_ref.state:
        from app.services.channels import add_global_required_channel

        try:
            await add_global_required_channel(db, event.bot, db_user.id, event.chat.id, scope="all")
            await event.bot.send_message(event.from_user.id, f"کانال اجباری ورود «{ch.title}» ثبت شد.")
        except Exception as exc:  # noqa: BLE001
            await event.bot.send_message(event.from_user.id, str(getattr(exc, "message", exc)))
        await state.clear()
        return
    try:
        await event.bot.send_message(
            event.from_user.id,
            f"ربات ادمین کانال «{ch.title}» شد و ذخیره گردید.\n"
            "موقع ثبت کاستوم از دکمه «استفاده از …» انتخابش کنید.",
        )
    except Exception:
        return


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
    regs = (
        await db.scalars(
            select(Registration).where(
                Registration.event_id == e.id, Registration.status == RegistrationStatus.CONFIRMED
            )
        )
    ).all()
    for reg in regs:
        user = await db.get(User, reg.user_id)
        if not user or user.is_bot_blocked:
            continue
        try:
            await cb.bot.send_message(
                user.telegram_id,
                f"کاستوم «{e.title}» لغو شد. آیدی و رمز ارسال نمی‌شود.",
            )
        except Exception:
            continue
    await cb.message.answer(f"کاستوم «{e.title}» لغو شد. به ثبت‌نام‌شده‌ها خبر داده شد.")
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
            "هنوز کانالی وصل نشده.\n"
            "دکمه زیر را بزنید تا ربات ادمین کانال شود؛ بعد موقع ثبت کاستوم همان کانال را انتخاب می‌کنید.",
            reply_markup=add_required_channel_kb(cancel=False),
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
        await message.answer("فرمت نامعتبر است. Room ID و رمز را در یک خط بفرستید؛ نمونه:\n<code>12345678 mypass</code>")
        return
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

