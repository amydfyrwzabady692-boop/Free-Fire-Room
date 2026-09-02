from __future__ import annotations

from datetime import UTC, date, datetime as dt, timedelta
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardMarkup, Message
from aiogram.enums import ChatMemberStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.access import is_active_admin, menu_for
from app.bot.helpers import event_deep_link, extract_channel_ref, esc, replace_callback_view
from app.bot.keyboards.common import (
    DANGER,
    PRIMARY,
    SUCCESS,
    add_required_channel_kb,
    creds_send_confirm_kb,
    event_share_kb,
    ibtn,
    labeled,
    MENU_BUTTON_TEXTS,
    organizer_home_kb,
    payout_contact_kb,
    pick_date_kb,
    social_review_kb,
    winner_claim_review_kb,
    winner_reply_kb,
    wizard_nav,
)
from app.locales.labels import event_status_fa, org_status_fa, reg_status_fa
from app.locales.style import room_pair
from app.bot.onboarding import ensure_onboarding, target_message
from app.bot.states.groups import (
    CredsWaitSG,
    EventWizardSG,
    OrganizerSettingsSG,
    WinnerChatSG,
)
from app.core.config import get_settings
from app.core.enums import (
    BanScope,
    EventStatus,
    OrganizerStatus,
    RegistrationStatus,
    SocialProofStatus,
    WinnerClaimStatus,
)
from app.core.errors import AppError
from app.core.time import combine_local_date_and_clock, format_jalali_date, format_local, parse_clock, upcoming_local_dates
from app.models.channel import Channel, ChannelOwnership
from app.models.event import Event, RoomCredential
from app.models.jobs import Delivery
from app.models.organizer import Organizer
from app.models.registration import Registration
from app.models.user import User
from app.services.bans import is_banned
from app.services.channels import connect_organizer_channel, list_owned_channels
from app.services.event_display import (
    default_custom_description,
    event_public_load_options,
    format_event_identity_block,
)
from app.services.events import (
    cancel_event,
    create_event,
    mark_event_started,
    submit_for_publish,
    update_credentials,
    waiting_live_credential_event,
)
from app.services.organizers import get_or_apply
from app.services.social import (
    PLATFORM_FA,
    normalize_social_url,
    pending_proof_count,
    pending_proofs_for_event,
    review_proof,
    social_required,
)
from app.services.winners import (
    claim_parties,
    claims_for_organizer,
    contact_link,
    format_payout_note,
    format_relayed_to_winner,
    normalize_payout_contact,
    player_dm_link,
    record_message,
    resolve_claim,
    resolve_payout_contact,
)
from app.services.registration import register_user
from app.services.reports import (
    credentials_window_open,
    creds_were_provided,
    format_person,
    is_archived,
)
from app.services.settings import get_setting

router = Router(name="organizer")

def _short_label(e: Event, limit: int = 50) -> str:
    return ((e.prize_summary or e.title or "کاستوم").strip())[:limit]


DEFAULT_RULES = (
    "برای دریافت ROOM ID و PASS باید تا لحظه ارسال در کانال‌های اجباری عضو بمانید "
    "و شرایط کاستوم را کامل کرده باشید. خروج از کانال قبل از ارسال یعنی مشخصات برایتان نمی‌آید."
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


async def _organizer_ready(db: AsyncSession, user: User, msg: Message) -> Organizer | None:
    org = await get_or_apply(db, user, user.first_name)
    if org.status == OrganizerStatus.PENDING:
        await msg.answer(
            "درخواست برگزارکننده شما ثبت شد و منتظر تأیید مدیریت است.\n"
            "بعد از تأیید، از همین «پنل برگزارکننده» کاستوم می‌گذارید."
        )
        return None
    if org.status in {OrganizerStatus.REJECTED, OrganizerStatus.SUSPENDED}:
        await msg.answer(
            f"حساب برگزارکننده شما {org_status_fa(org.status)} است. از «پشتیبانی» به مالک ربات پیام بدهید."
        )
        return None
    return org


@router.message(Command("host"))
@router.message(F.text.in_(labeled("ثبت کاستوم", "ثبت کاستوم جایزه‌دار")))
@router.callback_query(F.data == "orgp:new")
async def start_org(event: Message | CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    msg = target_message(event)
    if not await ensure_onboarding(msg, db_user, db):
        if isinstance(event, CallbackQuery):
            await event.answer()
        return
    if await _blocked_organize(db, db_user, event):
        return
    await state.clear()
    if await _organizer_ready(db, db_user, msg) is None:
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


@router.message(F.text.in_(labeled("پنل برگزارکننده", "کاستوم‌های من", "پنل برگزار کننده")))
@router.callback_query(F.data == "orgp:home")
async def org_home(event: Message | CallbackQuery, db: AsyncSession, db_user: User):
    msg = target_message(event)
    if not await ensure_onboarding(msg, db_user, db, recheck_channels=not isinstance(event, CallbackQuery)):
        if isinstance(event, CallbackQuery):
            await event.answer()
        return
    if await _blocked_organize(db, db_user, event):
        return
    if await _organizer_ready(db, db_user, msg) is None:
        if isinstance(event, CallbackQuery):
            await event.answer()
        return
    from app.services.reviews import format_rating_line, review_summary_for_organizer

    org = await db.scalar(select(Organizer).where(Organizer.user_id == db_user.id))
    rating = ""
    if org:
        rating = "\n" + format_rating_line(await review_summary_for_organizer(db, org.id), prefix="امتیاز شما از بازیکن‌ها")
    text = (
        "👑 <b>پنل برگزارکننده</b>\n"
        "<i>این پنل مالک ربات نیست؛ مخصوص کسی است که کاستوم می‌گذارد.</i>\n\n"
        "<b>۱) ثبت کاستوم جدید</b>\n"
        "ساعت، کانال جوین اجباری، جایزه، و در صورت تمایل توضیح و عکس.\n\n"
        "<b>۲) ارسال ROOM ID / PASS</b>\n"
        "لازم نیست سر ساعت آنلاین باشید — از همان لحظهٔ ساخت کاستوم می‌توانید مشخصات را ثبت کنید "
        "و ربات سر ساعت خودش برای واجدین شرایط می‌فرستد. نتیجه را هم به شما خبر می‌دهد.\n\n"
        "<b>۳) کاستوم‌ها و آمار من</b>\n"
        "چند نفر از لینک آمدند، چند نفر جوین کردند و چند نفر مشخصات گرفتند."
        f"{rating}"
    )
    if isinstance(event, CallbackQuery):
        await replace_callback_view(event, text, inline=organizer_home_kb())
        return
    await msg.answer(text, reply_markup=organizer_home_kb())


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
        f"🕐 تاریخ: {format_jalali_date(day)}\n"
        "حالا ساعت را بفرستید. نمونه: <code>22:00</code> یا <code>22</code>\n"
        "سر همین ساعت ROOM ID و PASS فقط برای کسانی می‌رود که کانال‌ها را جوین کرده باشند.",
        reply_markup=wizard_nav(include_back=True),
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
    if when < dt.now(UTC) - timedelta(minutes=1):
        await message.answer(
            "این ساعت گذشته است. ساعتی از الان به بعد بفرستید.\n"
            "محدودیتی ندارید — حتی همین چند دقیقه دیگر هم می‌شود."
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
            f"کانال «{esc(ch.title)}» به‌عنوان جوین اجباری ثبت شد.\n"
            "اگر کانال دیگری هم می‌خواهید همان روش را تکرار کنید.\n"
            "اگر تمام شد دکمه «تمام شد» را بزنید یا «-» بفرستید.",
            reply_markup=await _channel_step_kb(db, db_user, ids, extra=True),
        )
        return
    await bot.send_message(
        telegram_id,
        f"کانال «{esc(ch.title)}» اضافه شد ({len(ids)}/{max_ch}).\n"
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
    if text in labeled("-", "تمام شد", "ادامه"):
        if not ids:
            await message.answer("حداقل یک کانال لازم است.", reply_markup=await _channel_step_kb(db, db_user, [], extra=False))
            return
        await _ask_prize(message, state)
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
    await _ask_prize(cb.message, state)
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
    except Exception:  # noqa: BLE001
        try:
            await event.bot.send_message(
                event.from_user.id,
                f"ربات به کانال «{esc(event.chat.title)}» اضافه شد، ولی ثبت نشد. دوباره از پنل برگزارکننده وصل کنید.",
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
            await event.bot.send_message(event.from_user.id, f"کانال اجباری ورود «{esc(ch.title)}» ثبت شد.")
        except AppError as exc:
            await event.bot.send_message(event.from_user.id, exc.message)
        except Exception:
            await event.bot.send_message(event.from_user.id, "کانال ثبت نشد. دوباره تلاش کنید.")
        await state.clear()
        return
    try:
        await event.bot.send_message(
            event.from_user.id,
            f"ربات ادمین کانال «{esc(ch.title)}» شد و ذخیره گردید.\n"
            "موقع ثبت کاستوم از دکمه «استفاده از …» انتخابش کنید.",
        )
    except Exception:
        return


PRIZE_STEP_TEXT = (
    "💎 <b>جایزه این کاستوم چیست؟</b>\n\n"
    "این متن هم روی دکمهٔ کاستوم در فهرست دیده می‌شود و هم داخل کارت آن، "
    "پس کوتاه و روشن بنویسید.\n\n"
    "نمونه:\n"
    "• ۱۰۰۰ الماس\n"
    "• نفر اول ۵۰ الماس، نفر دوم اسکین\n"
    "• ۵۰ هزار تومان کارت‌به‌کارت"
)


PAYOUT_STEP_TEXT = (
    "\U0001F3C6 <b>آیدی دریافت جایزه</b>\n\n"
    "وقتی برنده‌ای را تأیید کنید، ربات همین آیدی را برایش می‌فرستد "
    "تا برای گرفتن جایزه به پی‌وی شما بیاید.\n\n"
    "یک آیدی بفرستید. نمونه: <code>@my_id</code>\n"
    "اگر قبلاً ثبت کرده‌اید، از دکمهٔ پایین همان را بزنید یا آیدی جدید بنویسید."
)


SOCIAL_STEP_TEXT = (
    "\U0001F4F8 <b>فالو اینستاگرام یا یوتیوب</b> (اختیاری)\n\n"
    "اگر می‌خواهید بازیکن‌ها علاوه بر جوین کانال، پیج شما را هم فالو کنند، "
    "آدرس پیج را همین‌جا بفرستید.\n"
    "نمونه: <code>https://instagram.com/mypage</code> یا <code>@mypage</code>\n\n"
    "بعد از آن، مرحلهٔ آخر هر بازیکن این می‌شود که اسکرین‌شات فالو کردن را بفرستد؛ "
    "اسکرین برای شما می‌آید و تا تأیید نکنید ثبت‌نامش قطعی نمی‌شود.\n\n"
    "اگر لازم ندارید «رد کردن» را بزنید — هیچ بازیکنی این مرحله را نمی‌بیند."
)


DESCRIPTION_STEP_TEXT = (
    "📝 <b>توضیح کاستوم</b> (اختیاری)\n\n"
    "یک یا دو خط دربارهٔ اینکه این کاستوم چیست و برای چه کسانی است.\n\n"
    "نمونه:\n"
    "• کاستوم کلن، شب جمعه ساعت ۱۰\n"
    "• تورنمنت دوئل مخصوص فالوورهای کانال\n"
    "• مود Clash Squad، بدون کاراکتر\n\n"
    "اگر توضیحی ندارید «رد کردن» را بزنید — چیزی از دست نمی‌دهید."
)


BANNER_STEP_TEXT = (
    "🖼 <b>عکس یا بنر کاستوم</b> (اختیاری)\n\n"
    "اگر بنر آماده دارید همین‌جا بفرستید؛ بالای کارت کاستوم به بازیکن‌ها نشان داده می‌شود.\n"
    "ربات خودش عکس نمی‌سازد. اگر ندارید «رد کردن» را بزنید — این آخرین مرحله است."
)


async def _ask_description(message: Message, state: FSMContext) -> None:
    await state.set_state(EventWizardSG.description)
    await message.answer(DESCRIPTION_STEP_TEXT, reply_markup=wizard_nav(include_skip=True, include_back=True))


async def _ask_prize(message: Message, state: FSMContext) -> None:
    await state.set_state(EventWizardSG.prizes)
    await message.answer(PRIZE_STEP_TEXT, reply_markup=wizard_nav(include_back=True))


async def _ask_payout(message: Message, state: FSMContext, db: AsyncSession, db_user: User) -> None:
    """Who an approved winner is told to message to collect the prize."""
    org = await db.scalar(select(Organizer).where(Organizer.user_id == db_user.id))
    saved = (org.payout_contact or "").strip() if org else ""
    await state.set_state(EventWizardSG.payout_contact)
    await message.answer(
        PAYOUT_STEP_TEXT,
        reply_markup=payout_contact_kb(saved=saved or None, username=db_user.username),
    )


async def _ask_social(message: Message, state: FSMContext) -> None:
    await state.set_state(EventWizardSG.social)
    await message.answer(SOCIAL_STEP_TEXT, reply_markup=wizard_nav(include_skip=True, include_back=True))


async def _ask_banner(message: Message, state: FSMContext) -> None:
    await state.set_state(EventWizardSG.banner)
    await message.answer(BANNER_STEP_TEXT, reply_markup=wizard_nav(include_skip=True, include_back=True))


@router.message(EventWizardSG.description)
async def wiz_description(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    text = (message.text or "").strip()
    if text in labeled("-", "رد کردن", "رد"):
        await state.update_data(custom_description=None)
        await _ask_banner(message, state)
        return
    if len(text) < 5:
        await message.answer(
            "توضیح را کمی کامل‌تر بنویسید (حداقل ۵ حرف)، یا «رد کردن» را بزنید.",
            reply_markup=wizard_nav(include_skip=True, include_back=True),
        )
        return
    if len(text) > 500:
        await message.answer(
            "توضیح کاستوم حداکثر ۵۰۰ حرف باشد.", reply_markup=wizard_nav(include_skip=True, include_back=True)
        )
        return
    await state.update_data(custom_description=text)
    await _ask_banner(message, state)


@router.message(EventWizardSG.prizes)
async def wiz_prize(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    text = (message.text or "").strip()
    if len(text) < 3:
        await message.answer("جایزه را کمی واضح‌تر بنویسید.", reply_markup=wizard_nav(include_back=True))
        return
    if len(text) > 400:
        await message.answer("متن جایزه حداکثر ۴۰۰ حرف باشد.", reply_markup=wizard_nav(include_back=True))
        return
    await state.update_data(prize_summary=text)
    await _ask_payout(message, state, db, db_user)


async def _apply_payout(
    message: Message, state: FSMContext, db: AsyncSession, db_user: User, contact: str
) -> None:
    org = await db.scalar(select(Organizer).where(Organizer.user_id == db_user.id))
    if org:
        org.payout_contact = contact
        await db.flush()
    await state.update_data(payout_contact=contact)
    await message.answer(f"✅ آیدی دریافت جایزه: <b>{esc(contact)}</b>")
    await _ask_social(message, state)


@router.message(EventWizardSG.payout_contact)
async def wiz_payout(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    try:
        contact = normalize_payout_contact(message.text or "")
    except AppError as exc:
        org = await db.scalar(select(Organizer).where(Organizer.user_id == db_user.id))
        saved = (org.payout_contact or "").strip() if org else ""
        await message.answer(
            exc.message,
            reply_markup=payout_contact_kb(saved=saved or None, username=db_user.username),
        )
        return
    await _apply_payout(message, state, db, db_user, contact)


@router.callback_query(EventWizardSG.payout_contact, F.data.in_({"payc:saved", "payc:self"}))
async def wiz_payout_shortcut(cb: CallbackQuery, state: FSMContext, db: AsyncSession, db_user: User):
    contact = ""
    if cb.data == "payc:self" and db_user.username:
        contact = f"@{db_user.username.lstrip('@')}"
    else:
        org = await db.scalar(select(Organizer).where(Organizer.user_id == db_user.id))
        contact = (org.payout_contact or "").strip() if org else ""
    if not contact:
        await cb.answer("آیدی ذخیره‌شده‌ای نیست. خودتان بنویسید.", show_alert=True)
        return
    await _apply_payout(cb.message, state, db, db_user, contact)
    await cb.answer("ثبت شد")


@router.message(EventWizardSG.social)
async def wiz_social(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    text = (message.text or "").strip()
    if text in labeled("-", "رد کردن", "رد", "ندارم"):
        await state.update_data(social_url=None, social_platform=None)
        await _ask_description(message, state)
        return
    try:
        url, platform = normalize_social_url(text)
    except AppError as exc:
        await message.answer(exc.message, reply_markup=wizard_nav(include_skip=True, include_back=True))
        return
    await state.update_data(social_url=url, social_platform=platform)
    await message.answer(
        f"✅ شرط فالو {PLATFORM_FA.get(platform, 'پیج')} ثبت شد:\n{esc(url)}\n\n"
        "هر بازیکن بعد از جوین کانال‌ها باید اسکرین فالو کردن را بفرستد و شما تأییدش کنید."
    )
    await _ask_description(message, state)


@router.message(EventWizardSG.banner)
async def wiz_banner(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    if message.photo:
        await state.update_data(banner_file_id=message.photo[-1].file_id)
        await _publish_custom(message, state, db, db_user)
        return
    if message.document and (message.document.mime_type or "").startswith("image/"):
        await state.update_data(banner_file_id=message.document.file_id)
        await _publish_custom(message, state, db, db_user)
        return
    await message.answer(
        "یک عکس بفرستید، یا دکمه «رد کردن» را بزنید.",
        reply_markup=wizard_nav(include_skip=True, include_back=True),
    )


@router.callback_query(F.data == "wiz:back")
async def wiz_back(cb: CallbackQuery, state: FSMContext, db: AsyncSession, db_user: User):
    current = await state.get_state()
    msg = cb.message
    if current == EventWizardSG.starts_time.state:
        await state.set_state(EventWizardSG.starts_at)
        await msg.answer("روز کاستوم را دوباره انتخاب کنید.", reply_markup=pick_date_kb("wzd"))
    elif current in {EventWizardSG.channel.state, EventWizardSG.extra_channels.state}:
        await state.update_data(required_channel_ids=[], channel_id=None)
        await state.set_state(EventWizardSG.starts_at)
        await msg.answer("روز کاستوم را دوباره انتخاب کنید.", reply_markup=pick_date_kb("wzd"))
    elif current == EventWizardSG.prizes.state:
        data = await state.get_data()
        ids = list(data.get("required_channel_ids") or [])
        await state.set_state(EventWizardSG.extra_channels)
        await msg.answer(
            "کانال‌های جوین اجباری را دوباره تنظیم کنید، بعد «تمام شد» را بزنید.",
            reply_markup=await _channel_step_kb(db, db_user, ids, extra=True),
        )
    elif current == EventWizardSG.payout_contact.state:
        await _ask_prize(msg, state)
    elif current == EventWizardSG.social.state:
        await _ask_payout(msg, state, db, db_user)
    elif current == EventWizardSG.description.state:
        await _ask_social(msg, state)
    elif current == EventWizardSG.banner.state:
        await _ask_description(msg, state)
    else:
        await cb.answer("مرحله قبلی ندارد.", show_alert=True)
        return
    await cb.answer()


@router.callback_query(F.data == "wiz:skip")
async def wiz_skip(cb: CallbackQuery, state: FSMContext, db: AsyncSession, db_user: User):
    current = await state.get_state()
    if current == EventWizardSG.social.state:
        await state.update_data(social_url=None, social_platform=None)
        await _ask_description(cb.message, state)
        await cb.answer()
        return
    if current == EventWizardSG.description.state:
        await state.update_data(custom_description=None)
        await _ask_banner(cb.message, state)
        await cb.answer()
        return
    if current == EventWizardSG.banner.state:
        await _publish_custom(cb.message, state, db, db_user)
        await cb.answer()
        return
    await cb.answer()


async def _publish_custom(message: Message, state: FSMContext, db: AsyncSession, db_user: User) -> None:
    data = await state.get_data()
    prize = (data.get("prize_summary") or "").strip()
    if not prize:
        await _ask_prize(message, state)
        return
    description = default_custom_description(
        custom_description=data.get("custom_description"),
        title=data.get("title"),
        channel_title=data.get("channel_title"),
    )
    org = await db.scalar(select(Organizer).where(Organizer.user_id == db_user.id))
    title = (data.get("title") or "").strip() or "کاستوم جایزه‌دار"
    starts_at = dt.fromisoformat(data["starts_at"])
    fill_end = starts_at + timedelta(hours=get_settings().auto_archive_hours)
    payload = {
        "title": title[:160],
        "starts_at": starts_at,
        "registration_ends_at": fill_end,
        "credentials_send_at": starts_at,
        "channel_id": UUID(data["channel_id"]),
        "required_channel_ids": [UUID(x) for x in data.get("required_channel_ids") or []],
        # no cap: everyone who completes the conditions gets in
        "capacity": 0,
        "payout_contact": data.get("payout_contact"),
        "social_url": data.get("social_url"),
        "social_platform": data.get("social_platform"),
        "region": "ME",
        "game_mode": "squad",
        "prize_summary": prize,
        "description": description,
        "banner_file_id": data.get("banner_file_id"),
        "prizes": [{"place": 1, "title": prize[:160], "description": prize}],
        "rules_text": DEFAULT_RULES,
        "require_rules_accept": False,
        "required_referrals": 0,
        "waitlist_enabled": True,
        "visibility": "public",
    }
    try:
        event = await create_event(db, org, payload, db_user.id)
        await submit_for_publish(db, event, db_user.id)
        event = await db.scalar(
            select(Event).where(Event.id == event.id).options(*event_public_load_options())
        )
        link = event_deep_link(event.public_token)
        n_ch = len(payload["required_channel_ids"])
        social_line = (
            f"📸 فالو {PLATFORM_FA.get(event.social_platform, 'پیج')}: {esc(event.social_url)}\n"
            if social_required(event)
            else ""
        )
        payout_line = (
            f"🏆 آیدی دریافت جایزه: {esc(event.payout_contact)}\n" if event.payout_contact else ""
        )
        details = (
            f"{format_event_identity_block(event)}\n"
            f"🕐 {format_local(event.starts_at, event.timezone)}\n"
            f"📢 کانال جوین اجباری: {n_ch} مورد\n"
            "👥 ظرفیت: بدون محدودیت\n"
            f"{social_line}{payout_line}\n"
            f"<b>لینک این کاستوم:</b>\n{link}\n\n"
            "📌 لینک را در کانال خودتان بگذارید تا بازیکن‌ها وارد شوند.\n\n"
            "🆔 <b>قدم بعدی:</b> از «ارسال ROOM ID / PASS» می‌توانید <b>همین حالا</b> مشخصات اتاق را ثبت کنید؛ "
            "ربات سر ساعت خودکار برای واجدین شرایط می‌فرستد و نتیجه را به شما خبر می‌دهد.\n\n"
            "⏹ <b>مهم:</b> این کاستوم تا وقتی خودتان دکمهٔ «کاستوم شروع شد» را نزنید "
            "در فهرست «کاستوم‌های پیش‌رو» می‌ماند و ثبت‌نام باز است. "
            "هر وقت بازی را شروع کردید، از «کاستوم‌ها و آمار من» آن دکمه را بزنید تا به «گذشته» برود."
        )
        banner = data.get("banner_file_id")
        if banner:
            try:
                await message.answer_photo(banner)
            except Exception:
                pass
        await message.answer(details, reply_markup=event_share_kb(link))
        if event.status == EventStatus.PUBLISHED:
            await message.answer(
                "کاستوم در فهرست همه قرار گرفت.",
                reply_markup=await menu_for(db, db_user),
            )
        else:
            await message.answer(
                "کاستوم ثبت شد و منتظر تأیید مدیر است.",
                reply_markup=await menu_for(db, db_user),
            )
    except AppError as exc:
        await message.answer(exc.message)
        return
    except Exception:
        await message.answer("ثبت کاستوم الان انجام نشد. چند ثانیه بعد دوباره تلاش کنید.")
        return
    await state.clear()


@router.callback_query(F.data == "orgp:mine")
async def org_mine(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if await _blocked_organize(db, db_user, cb):
        return
    org = await db.scalar(select(Organizer).where(Organizer.user_id == db_user.id))
    if not org:
        await cb.answer("اول یک کاستوم بسازید.", show_alert=True)
        return
    rows = (
        await db.scalars(
            select(Event)
            .where(Event.organizer_id == org.id, Event.deleted_at.is_(None))
            .options(*event_public_load_options())
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
        creds = await db.scalar(select(RoomCredential).where(RoomCredential.event_id == e.id))
        can_send = (
            e.status not in {EventStatus.CANCELLED, EventStatus.FINISHED, EventStatus.REJECTED}
            and (credentials_window_open(e) or creds_were_provided(creds))
        )
        archived = is_archived(e)
        pending_social = await pending_proof_count(db, e.id) if social_required(e) else 0
        buttons = []
        if can_send:
            buttons.append([ibtn("ارسال ROOM ID / PASS", callback_data=f"orgp:creds:{e.public_token}", style=SUCCESS)])
        if pending_social:
            buttons.append(
                [
                    ibtn(
                        f"اسکرین فالو در انتظار ({pending_social})",
                        callback_data=f"orgp:soc:{e.public_token}",
                        style=SUCCESS,
                    )
                ]
            )
        buttons.append(
            [
                ibtn("لینک اختصاصی", callback_data=f"orgp:link:{e.public_token}", style=PRIMARY),
                ibtn("قیف و آمار", callback_data=f"orgp:fun:{e.public_token}", style=PRIMARY),
            ]
        )
        buttons.append([ibtn("خروجی شرکت‌کننده‌ها", callback_data=f"orgp:csv:{e.public_token}", style=PRIMARY)])
        if not archived and e.status not in {EventStatus.CANCELLED, EventStatus.FINISHED}:
            buttons.append(
                [ibtn("کاستوم شروع شد — انتقال به گذشته", callback_data=f"orgp:start:{e.public_token}", style=DANGER)]
            )
        if e.status not in {EventStatus.CANCELLED, EventStatus.FINISHED}:
            buttons.append([ibtn("لغو کاستوم", callback_data=f"orgp:cancel:{e.public_token}", style=DANGER)])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        where = (
            "\U0001F4E5 در فهرست «گذشته»"
            if archived
            else "\U0001F525 در فهرست «کاستوم‌های پیش‌رو» — ثبت‌نام باز است"
        )
        await cb.message.answer(
            f"{format_event_identity_block(e)}\n"
            f"زمان (شمسی): {format_local(e.starts_at, e.timezone)}\n"
            f"وضعیت: {event_status_fa(e.status)}\n"
            f"{where}\n"
            f"{format_audience_stats(stats)}\n"
            f"{rating}",
            reply_markup=kb,
        )
    await cb.message.answer("بازگشت به پنل:", reply_markup=organizer_home_kb())
    await cb.answer()


async def _own_event(db: AsyncSession, db_user: User, token: str) -> Event | None:
    e = await db.scalar(
        select(Event).where(Event.public_token == token).options(*event_public_load_options())
    )
    if not e or not e.organizer or e.organizer.user_id != db_user.id:
        return None
    return e


@router.callback_query(F.data.startswith("orgp:fun:"))
async def org_funnel(cb: CallbackQuery, db: AsyncSession, db_user: User):
    """Where the organizer is losing people - every number already existed."""
    from app.services.funnel import biggest_drop, event_funnel, format_funnel

    token = cb.data.split(":", 2)[-1]
    e = await _own_event(db, db_user, token)
    if not e:
        await cb.answer("یافت نشد", show_alert=True)
        return
    stats = await event_funnel(db, e.id)
    hint = biggest_drop(stats)
    text = (
        f"<b>{esc(_short_label(e))}</b>\n"
        f"🕐 {format_local(e.starts_at, e.timezone)}\n"
        "━━━━━━━━━━━━━━\n"
        f"{format_funnel(stats)}"
    )
    if hint:
        text += f"\n\n💡 {hint}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn("خروجی شرکت‌کننده‌ها", callback_data=f"orgp:csv:{e.public_token}", style=PRIMARY)],
            [ibtn("بازگشت به پنل", callback_data="orgp:home", style=PRIMARY)],
        ]
    )
    await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("orgp:csv:"))
async def org_participants_csv(cb: CallbackQuery, db: AsyncSession, db_user: User):
    """The web API could already export this; the bot could not."""
    import csv
    import io as _io

    from aiogram.types import BufferedInputFile

    token = cb.data.split(":", 2)[-1]
    e = await _own_event(db, db_user, token)
    if not e:
        await cb.answer("یافت نشد", show_alert=True)
        return
    rows = (
        await db.scalars(
            select(Registration)
            .where(Registration.event_id == e.id)
            .options(selectinload(Registration.user))
            .order_by(Registration.created_at.asc())
        )
    ).all()
    if not rows:
        await cb.answer("هنوز کسی ثبت‌نام نکرده.", show_alert=True)
        return
    delivered = set(
        (
            await db.scalars(
                select(Delivery.user_id).where(
                    Delivery.event_id == e.id,
                    Delivery.kind == "room_credentials",
                    Delivery.status == "sent",
                )
            )
        ).all()
    )
    buffer = _io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["telegram_id", "name", "username", "status", "source", "got_credentials", "joined_at"])
    for reg in rows:
        u = reg.user
        writer.writerow(
            [
                u.telegram_id if u else "",
                (u.first_name or "") if u else "",
                (u.username or "") if u else "",
                reg_status_fa(reg.status),
                reg.source or "",
                "yes" if reg.user_id in delivered else "no",
                reg.created_at.isoformat(timespec="minutes"),
            ]
        )
    # BOM so Excel opens the Persian columns correctly
    data = ("\ufeff" + buffer.getvalue()).encode("utf-8")
    name = f"participants-{e.public_token[:8]}.csv"
    await cb.message.answer_document(
        BufferedInputFile(data, filename=name),
        caption=f"شرکت‌کننده‌های «{esc(_short_label(e))}» — {len(rows)} نفر",
    )
    await cb.answer()


@router.callback_query(F.data.startswith("orgp:link:"))
async def org_link(cb: CallbackQuery, db: AsyncSession, db_user: User):
    token = cb.data.split(":", 2)[-1]
    e = await db.scalar(
        select(Event)
        .where(Event.public_token == token)
        .options(*event_public_load_options(), selectinload(Event.required_channels))
    )
    if not e or not e.organizer or e.organizer.user_id != db_user.id:
        await cb.answer("یافت نشد", show_alert=True)
        return
    link = event_deep_link(e.public_token)
    n_ch = len([c for c in (e.required_channels or []) if c.is_active])
    details = (
        f"{format_event_identity_block(e)}\n"
        f"🕐 {format_local(e.starts_at, e.timezone)}\n"
        f"📢 کانال جوین اجباری: {n_ch} مورد\n\n"
        f"<b>لینک اختصاصی:</b>\n{link}\n\n"
        "هر کس این لینک را باز کند مستقیم وارد کارت همین کاستوم می‌شود."
    )
    if e.banner_file_id:
        try:
            await cb.message.answer_photo(e.banner_file_id)
        except Exception:
            pass
    await cb.message.answer(details, reply_markup=event_share_kb(link))
    await cb.answer()


@router.callback_query(F.data.startswith("orgp:cancel:"))
async def org_cancel(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if await _blocked_organize(db, db_user, cb):
        return
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
                Registration.event_id == e.id,
                Registration.status.in_(
                    [RegistrationStatus.CONFIRMED, RegistrationStatus.WAITLISTED, RegistrationStatus.PENDING]
                ),
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
                f"کاستوم «{esc(e.title)}» لغو شد. ROOM ID و PASS ارسال نمی‌شود.",
            )
        except Exception:
            continue
    await cb.message.answer(f"کاستوم «{esc(e.title)}» لغو شد. به ثبت‌نام‌شده‌ها خبر داده شد.")
    await cb.answer()


@router.callback_query(F.data == "orgp:ch")
async def org_channels(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if await _blocked_organize(db, db_user, cb):
        return
    rows = (
        await db.scalars(
            select(ChannelOwnership)
            .where(ChannelOwnership.user_id == db_user.id, ChannelOwnership.is_active.is_(True))
            .options(selectinload(ChannelOwnership.channel))
        )
    ).all()
    if not rows:
        kb = add_required_channel_kb(cancel=False)
        rows = list(kb.inline_keyboard)
        rows.append([ibtn("بازگشت به پنل", callback_data="orgp:home", style=DANGER)])
        await cb.message.answer(
            "هنوز کانالی وصل نشده.\n"
            "دکمه زیر را بزنید تا ربات ادمین کانال شود؛ بعد موقع ثبت کاستوم همان کانال را انتخاب می‌کنید.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await cb.answer()
        return
    text = "کانال‌های تأییدشده شما:\n"
    for own in rows:
        ch = own.channel
        if not ch:
            continue
        admin = "ادمین ربات: بله" if ch.bot_is_admin else "ادمین ربات: خیر — عضویت قابل بررسی نیست"
        text += f"• {esc(ch.title)} (@{esc(ch.username or '-')}) — {admin}\n"
    await cb.message.answer(text, reply_markup=organizer_home_kb())
    await cb.answer()


async def _organizer_events_for_creds(db: AsyncSession, user_id) -> list[Event]:
    org = await db.scalar(select(Organizer).where(Organizer.user_id == user_id))
    if not org:
        return []
    rows = (
        await db.scalars(
            select(Event)
            .where(Event.organizer_id == org.id, Event.deleted_at.is_(None))
            .order_by(Event.starts_at.desc())
            .limit(15)
        )
    ).all()
    out: list[Event] = []
    for e in rows:
        creds = await db.scalar(select(RoomCredential).where(RoomCredential.event_id == e.id))
        if e.status in {EventStatus.CANCELLED, EventStatus.FINISHED, EventStatus.REJECTED}:
            continue
        if credentials_window_open(e) or creds_were_provided(creds):
            out.append(e)
    return out


@router.callback_query(F.data == "orgp:creds_menu")
async def org_creds_menu(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if await _blocked_organize(db, db_user, cb):
        return
    if await _organizer_ready(db, db_user, cb.message) is None:
        await cb.answer()
        return
    rows = await _organizer_events_for_creds(db, db_user.id)
    if not rows:
        await cb.message.answer(
            "الان کاستومی برای ارسال ROOM ID / PASS ندارید.\n"
            "اول از «ثبت کاستوم جدید» یک کاستوم بسازید؛ بلافاصله بعدش همین‌جا ظاهر می‌شود "
            "و از همان لحظه تا وقتی «کاستوم شروع شد» را نزده‌اید می‌توانید مشخصات را ثبت کنید.",
            reply_markup=organizer_home_kb(),
        )
        await cb.answer()
        return
    from app.services.reviews import event_audience_stats, format_audience_stats

    await cb.message.answer(
        "🎮 <b>ارسال ROOM ID / PASS</b>\n\n"
        "لازم نیست سر ساعت آنلاین باشید: <b>همین حالا</b> هم می‌توانید ثبت کنید.\n"
        "• اگر قبل از ساعت کاستوم ثبت کنید، ربات خودش سر ساعت برای واجدین شرایط می‌فرستد.\n"
        "• اگر بعد از ساعت کاستوم ثبت کنید، بلافاصله فرستاده می‌شود.\n\n"
        "در هر دو حالت اول پیش‌نمایش می‌بینید و تا شما تأیید نکنید چیزی ارسال نمی‌شود."
    )
    for e in rows:
        stats = await event_audience_stats(db, e.id)
        creds = await db.scalar(select(RoomCredential).where(RoomCredential.event_id == e.id))
        sent_note = " (قبلاً ارسال شده — با تأیید دوباره، اصلاح هم پخش می‌شود)" if creds and creds.sent_at else ""
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [ibtn("ارسال ROOM ID / PASS", callback_data=f"orgp:creds:{e.public_token}", style=SUCCESS)],
                [ibtn("بازگشت", callback_data="orgp:home", style=PRIMARY)],
            ]
        )
        await cb.message.answer(
            f"<b>{esc(e.title)}</b>{sent_note}\n"
            f"زمان: {format_local(e.starts_at, e.timezone)}\n"
            f"وضعیت: {event_status_fa(e.status)}\n"
            f"{format_audience_stats(stats)}",
            reply_markup=kb,
        )
    await cb.answer()


@router.callback_query(F.data.startswith("orgp:creds:"))
async def ask_live_creds(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    if await _blocked_organize(db, db_user, cb):
        return
    token = cb.data.split(":", 2)[-1]
    e = await db.scalar(select(Event).where(Event.public_token == token).options(selectinload(Event.organizer)))
    if not e or not e.organizer or e.organizer.user_id != db_user.id:
        await cb.answer("یافت نشد", show_alert=True)
        return
    creds = await db.scalar(select(RoomCredential).where(RoomCredential.event_id == e.id))
    if not credentials_window_open(e) and not creds_were_provided(creds):
        await cb.answer("این کاستوم بسته شده و دیگر نمی‌شود مشخصات فرستاد.", show_alert=True)
        return
    await state.set_state(CredsWaitSG.room_id)
    await state.update_data(event_token=token, room_id=None)
    started = dt.now(UTC) >= e.starts_at
    timing = (
        "⏳ ساعت کاستوم رسیده. با تأیید، همین حالا برای واجدین شرایط ارسال می‌شود."
        if started
        else "⏳ هنوز به ساعت کاستوم نرسیده‌ایم. با تأیید، مشخصات ذخیره می‌شود و "
        f"سر ساعت {format_local(e.starts_at, e.timezone)} خودکار ارسال می‌شود."
    )
    await cb.message.answer(
        f"🎮 کاستوم «{esc(e.title)}»\n\n"
        "اول فقط <b>ROOM ID</b> را بفرستید.\n"
        "نمونه: <code>12345678</code>\n\n"
        "بعد ربات <b>PASS</b> را جدا می‌پرسد.\n"
        "در پایان پیش‌نمایش می‌بینید و تا تأیید نکنید چیزی ارسال نمی‌شود.\n\n"
        f"{timing}\n"
        "⏳ عجله‌ای نیست: تا وقتی دکمهٔ «کاستوم شروع شد» را نزده‌اید، هم می‌توانید مشخصات را ثبت کنید "
        "و هم بازیکن‌های تازه ثبت‌نام می‌شوند.",
        reply_markup=wizard_nav(),
    )
    await cb.answer()


def _parse_room_id(text: str | None) -> str | None:
    raw = (text or "").strip()
    if raw.isdigit() and 4 <= len(raw) <= 16:
        return raw
    return None


def _looks_like_room_creds(text: str | None) -> tuple[str, str] | None:
    parts = (text or "").split(maxsplit=1)
    if len(parts) != 2:
        return None
    room_id, password = parts[0].strip(), parts[1].strip()
    if not room_id.isdigit() or not (4 <= len(room_id) <= 16) or len(password) < 1:
        return None
    return room_id, password


# The ROOM ID / PASS typed by the organizer live here between the preview and
# the confirm tap. FSM data alone is not enough: any menu button the organizer
# taps in between runs a handler that calls state.clear().
PENDING_CREDS_TTL_SECONDS = 45 * 60


def _pending_creds_key(event: Event) -> str:
    return f"pending_creds:{event.id}"


async def _store_pending_creds(event: Event, room_id: str, password: str, user_id) -> None:
    import json

    from app.core.redis import get_redis
    from app.core.security import encrypt_secret

    blob = encrypt_secret(json.dumps({"room_id": room_id, "password": password, "by": str(user_id)}))
    try:
        await get_redis().setex(_pending_creds_key(event), PENDING_CREDS_TTL_SECONDS, blob)
    except Exception:  # noqa: BLE001
        pass


async def _load_pending_creds(event: Event, user_id) -> tuple[str, str] | None:
    import json

    from app.core.redis import get_redis
    from app.core.security import decrypt_secret

    try:
        blob = await get_redis().get(_pending_creds_key(event))
    except Exception:  # noqa: BLE001
        return None
    if not blob:
        return None
    try:
        data = json.loads(decrypt_secret(blob))
    except (ValueError, TypeError):
        return None
    if data.get("by") != str(user_id):
        return None
    room_id = (data.get("room_id") or "").strip()
    password = (data.get("password") or "").strip()
    return (room_id, password) if room_id and password else None


async def _clear_pending_creds(event: Event) -> None:
    from app.core.redis import get_redis

    try:
        await get_redis().delete(_pending_creds_key(event))
    except Exception:  # noqa: BLE001
        pass


async def _offer_creds_confirm(
    message: Message,
    state: FSMContext,
    db: AsyncSession,
    db_user: User,
    event: Event,
    room_id: str,
    password: str,
) -> bool:
    creds = await db.scalar(select(RoomCredential).where(RoomCredential.event_id == event.id))
    if not credentials_window_open(event) and not creds_were_provided(creds):
        await message.answer(
            "این کاستوم بسته شده است. دیگر نمی‌توانید ROOM ID و PASS بفرستید."
        )
        return False
    from app.services.reviews import event_audience_stats

    stats = await event_audience_stats(db, event.id)
    now = dt.now(UTC)
    scheduled = now < event.credentials_send_at
    await state.update_data(
        event_token=event.public_token,
        room_id=room_id,
        pending_password=password,
    )
    await _store_pending_creds(event, room_id, password, db_user.id)
    when = (
        f"⏰ ارسال زمان‌بندی‌شده: {format_local(event.credentials_send_at, event.timezone)}\n"
        "با تأیید، مشخصات ذخیره می‌شود و سر همان ساعت برای واجدین شرایط ارسال می‌شود."
        if scheduled
        else "با تأیید، همین الان برای کسانی که شرایط را کامل کرده‌اند ارسال می‌شود."
    )
    extra = (
        "\nتا وقتی «کاستوم شروع شد» را نزده‌اید، اگر کسی دیرتر شرایط را کامل کند "
        "مشخصات برایش هم می‌آید."
        if not scheduled
        else ""
    )
    resend = "\n⚠️ قبلاً ارسال شده — با تأیید، نسخه جدید (اصلاح) هم پخش می‌شود." if creds and creds.sent_at else ""
    await message.answer(
        "📋 <b>پیش‌نمایش ارسال ROOM ID / PASS</b>\n\n"
        f"کاستوم: <b>{esc(event.title)}</b>\n"
        f"{room_pair(esc(room_id), esc(password))}\n\n"
        f"👥 شرایط را کامل کردند: <b>{stats['confirmed']}</b>\n"
        f"⏳ هنوز جوین نکرده‌اند: {stats['pending']}\n"
        f"✅ قبلاً دریافت کردند: {stats['delivered']}\n\n"
        f"{when}{extra}{resend}\n\n"
        "فقط کاربرانی که <b>عضو کانال‌های اجباری</b> باشند در لحظه ارسال، ROOM ID / PASS می‌گیرند.",
        reply_markup=creds_send_confirm_kb(event.public_token),
    )
    return True


async def _commit_creds_send(
    message: Message,
    db: AsyncSession,
    db_user: User,
    event: Event,
    room_id: str,
    password: str,
) -> None:
    await update_credentials(db, event, db_user.id, room_id, password)
    await db.commit()
    now = dt.now(UTC)
    if now < event.credentials_send_at:
        await message.answer(
            "✅ تأیید شد و ذخیره گردید.\n"
            f"سر ساعت {format_local(event.credentials_send_at, event.timezone)} "
            "برای کسانی که تا آن لحظه شرایط را کامل کرده‌اند ارسال می‌شود.\n\n"
            f"{room_pair(esc(room_id), esc(password))}",
            reply_markup=await menu_for(db, db_user),
        )
        return
    from app.workers.enqueue import spawn
    from app.workers.tasks import send_event_credentials

    spawn(send_event_credentials, str(event.id))
    await message.answer(
        "✅ تأیید شد. در حال ارسال برای کسانی که شرایط را کامل کرده‌اند.\n"
        "تا وقتی «کاستوم شروع شد» را نزده‌اید، هر کس شرایط را کامل کند مشخصات برایش می‌آید.\n\n"
        f"{room_pair(esc(room_id), esc(password))}",
        reply_markup=await menu_for(db, db_user),
    )


async def _save_and_dispatch_creds(
    message: Message,
    state: FSMContext,
    db: AsyncSession,
    db_user: User,
    event: Event,
    room_id: str,
    password: str,
) -> bool:
    ok = await _offer_creds_confirm(message, state, db, db_user, event, room_id, password)
    if ok:
        # a real state (not default) so MenuResetMiddleware keeps the pending
        # ROOM ID / PASS if the organizer taps a menu button before confirming
        await state.set_state(CredsWaitSG.confirm)
    return ok


@router.callback_query(F.data.startswith("orgp:sendok:"))
async def confirm_creds_send(cb: CallbackQuery, state: FSMContext, db: AsyncSession, db_user: User):
    if await _blocked_organize(db, db_user, cb):
        return
    token = cb.data.split(":", 2)[-1]
    e = await db.scalar(select(Event).where(Event.public_token == token).options(selectinload(Event.organizer)))
    if not e or not e.organizer or e.organizer.user_id != db_user.id:
        await cb.answer("کاستوم یافت نشد.", show_alert=True)
        await state.clear()
        return
    pending = await _load_pending_creds(e, db_user.id)
    if pending is None:
        data = await state.get_data()
        room_id = (data.get("room_id") or "").strip()
        password = (data.get("pending_password") or "").strip()
    else:
        room_id, password = pending
    if not room_id or not password:
        await cb.answer("ROOM ID یا PASS یافت نشد. دوباره از منو شروع کنید.", show_alert=True)
        await state.clear()
        return
    creds = await db.scalar(select(RoomCredential).where(RoomCredential.event_id == e.id))
    if not credentials_window_open(e) and not creds_were_provided(creds):
        await cb.answer("مهلت ارسال تمام شده.", show_alert=True)
        await state.clear()
        return
    await _commit_creds_send(cb.message, db, db_user, e, room_id, password)
    await _clear_pending_creds(e)
    await state.clear()
    await cb.answer("ارسال تأیید شد")


@router.message(CredsWaitSG.confirm, ~F.text.in_(MENU_BUTTON_TEXTS))
async def creds_awaiting_confirm(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    data = await state.get_data()
    token = data.get("event_token")
    room_id = (data.get("room_id") or "").strip()
    password = (data.get("pending_password") or "").strip()
    if not token or not room_id or not password:
        await state.clear()
        await message.answer(
            "مشخصات در دسترس نیست. از «ارسال ROOM ID / PASS» دوباره شروع کنید.",
            reply_markup=organizer_home_kb(),
        )
        return
    parsed = _looks_like_room_creds(message.text) or (
        (_parse_room_id(message.text), None) if _parse_room_id(message.text) else None
    )
    if parsed and parsed[1]:
        e = await db.scalar(
            select(Event).where(Event.public_token == token).options(selectinload(Event.organizer))
        )
        if e and e.organizer and e.organizer.user_id == db_user.id:
            await _save_and_dispatch_creds(message, state, db, db_user, e, parsed[0], parsed[1])
            return
    await message.answer(
        "برای ارسال، دکمه سبز «تأیید و ارسال» را بزنید.\n"
        "اگر می‌خواهید ROOM ID یا PASS را عوض کنید، «انصراف» را بزنید و دوباره شروع کنید.",
        reply_markup=creds_send_confirm_kb(token),
    )


@router.callback_query(F.data == "orgp:sendcancel")
async def cancel_creds_send(cb: CallbackQuery, state: FSMContext, db: AsyncSession, db_user: User):
    data = await state.get_data()
    token = data.get("event_token")
    if token:
        e = await db.scalar(select(Event).where(Event.public_token == token))
        if e:
            await _clear_pending_creds(e)
    await state.update_data(pending_password=None, room_id=None, event_token=None)
    await state.clear()
    await cb.message.answer("ارسال ROOM ID / PASS لغو شد.", reply_markup=organizer_home_kb())
    await cb.answer()


@router.message(CredsWaitSG.room_id)
async def receive_room_id(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    if await _blocked_organize(db, db_user, message):
        await state.clear()
        return
    parsed = _looks_like_room_creds(message.text)
    if parsed:
        data = await state.get_data()
        token = data.get("event_token")
        e = await db.scalar(select(Event).where(Event.public_token == token).options(selectinload(Event.organizer)))
        if not e or not e.organizer or e.organizer.user_id != db_user.id:
            await state.clear()
            await message.answer("کاستوم یافت نشد.")
            return
        await _save_and_dispatch_creds(message, state, db, db_user, e, parsed[0], parsed[1])
        return
    room_id = _parse_room_id(message.text)
    if not room_id:
        await message.answer(
            "ROOM ID نامعتبر است. فقط عدد بفرستید؛ نمونه: <code>12345678</code>",
            reply_markup=wizard_nav(),
        )
        return
    await state.update_data(room_id=room_id)
    await state.set_state(CredsWaitSG.password)
    await message.answer(
        f"🆔 ROOM ID ثبت شد: <code>{esc(room_id)}</code>\n\n"
        "حالا <b>PASS</b> را بفرستید.",
        reply_markup=wizard_nav(),
    )


@router.message(CredsWaitSG.password)
async def receive_room_password(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    if await _blocked_organize(db, db_user, message):
        await state.clear()
        return
    password = (message.text or "").strip()
    if not password or len(password) > 64:
        await message.answer("PASS را در یک پیام کوتاه بفرستید.", reply_markup=wizard_nav())
        return
    data = await state.get_data()
    token = data.get("event_token")
    room_id = data.get("room_id")
    if not room_id:
        await state.set_state(CredsWaitSG.room_id)
        await message.answer("اول ROOM ID را بفرستید.", reply_markup=wizard_nav())
        return
    e = await db.scalar(select(Event).where(Event.public_token == token).options(selectinload(Event.organizer)))
    if not e or not e.organizer or e.organizer.user_id != db_user.id:
        await state.clear()
        await message.answer("کاستوم یافت نشد.")
        return
    await _save_and_dispatch_creds(message, state, db, db_user, e, room_id, password)


@router.message(StateFilter(default_state), F.text.regexp(r"^\d{4,16}(\s+\S+)?$"))
async def maybe_live_creds(message: Message, db: AsyncSession, db_user: User, state: FSMContext):
    if await is_banned(db, db_user, BanScope.ORGANIZE):
        return
    e = await waiting_live_credential_event(db, db_user.id)
    if not e:
        return
    now = dt.now(UTC)
    if now < e.starts_at:
        return
    if not credentials_window_open(e):
        creds = await db.scalar(select(RoomCredential).where(RoomCredential.event_id == e.id))
        if not creds_were_provided(creds):
            return
    parsed = _looks_like_room_creds(message.text)
    if parsed:
        await _save_and_dispatch_creds(message, state, db, db_user, e, parsed[0], parsed[1])
        return
    room_id = _parse_room_id(message.text)
    if not room_id:
        return
    await state.set_state(CredsWaitSG.password)
    await state.update_data(event_token=e.public_token, room_id=room_id)
    await message.answer(
        f"🆔 ROOM ID ثبت شد: <code>{esc(room_id)}</code>\n\nحالا <b>PASS</b> را بفرستید.",
        reply_markup=wizard_nav(),
    )


# ---------------------------------------------------------------- start / archive


@router.callback_query(F.data.startswith("orgp:start:"))
async def org_mark_started(cb: CallbackQuery, db: AsyncSession, db_user: User):
    """The organizer, not the clock, decides when a custom is over."""
    if await _blocked_organize(db, db_user, cb):
        return
    token = cb.data.split(":", 2)[-1]
    e = await _own_event(db, db_user, token)
    if not e:
        await cb.answer("یافت نشد", show_alert=True)
        return
    if e.status in {EventStatus.CANCELLED, EventStatus.REJECTED}:
        await cb.answer("این کاستوم لغو شده است.", show_alert=True)
        return
    if e.archived_at is not None:
        await cb.answer("قبلاً به «گذشته» رفته است.", show_alert=True)
        return
    creds = await db.scalar(select(RoomCredential).where(RoomCredential.event_id == e.id))
    if not creds_were_provided(creds):
        await cb.answer(
            "هنوز ROOM ID / PASS نفرستاده‌اید. اول مشخصات را بفرستید، بعد شروع را بزنید.",
            show_alert=True,
        )
        return
    await mark_event_started(db, e, db_user.id)
    await db.commit()
    await cb.message.answer(
        f"⏹ کاستوم «{esc(_short_label(e))}» شروع‌شده ثبت شد.\n"
        "از «کاستوم‌های پیش‌رو» برداشته شد و حالا در «کاستوم‌های ۲۴ ساعت گذشته» دیده می‌شود.\n"
        "ثبت‌نام جدید و ارسال ROOM ID / PASS برای این کاستوم بسته شد.",
        reply_markup=organizer_home_kb(),
    )
    await cb.answer("ثبت شد")


# ---------------------------------------------------------------- follow screenshots


def _social_caption(event: Event, player: User) -> str:
    return (
        "📸 <b>اسکرین فالو</b>\n"
        f"کاستوم: {esc(_short_label(event))}\n"
        f"بازیکن: {format_person(player)}\n"
        f"پیج: {esc(event.social_url or '—')}\n\n"
        "اگر درست است «تأیید ثبت‌نام» را بزنید تا ثبت‌نامش قطعی شود و سر ساعت ROOM ID / PASS برایش برود."
    )


@router.callback_query(F.data.startswith("orgp:soc:"))
async def org_social_queue(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if await _blocked_organize(db, db_user, cb):
        return
    token = cb.data.split(":", 2)[-1]
    e = await _own_event(db, db_user, token)
    if not e:
        await cb.answer("یافت نشد", show_alert=True)
        return
    rows = await pending_proofs_for_event(db, e.id, limit=10)
    if not rows:
        await cb.message.answer("اسکرین در انتظاری نیست.", reply_markup=organizer_home_kb())
        await cb.answer()
        return
    await cb.message.answer(
        f"📸 <b>اسکرین‌های فالو در انتظار</b> — {len(rows)} مورد\n"
        "هر کدام را ببینید و تأیید یا رد کنید. تا تأیید نکنید ثبت‌نام آن بازیکن قطعی نمی‌شود."
    )
    for proof in rows:
        caption = _social_caption(e, proof.user)
        try:
            await cb.message.answer_photo(
                proof.file_id, caption=caption[:1024], reply_markup=social_review_kb(str(proof.id))
            )
        except Exception:  # noqa: BLE001
            await cb.message.answer(
                caption + "\n\n<i>اسکرین قابل نمایش نیست.</i>",
                reply_markup=social_review_kb(str(proof.id)),
            )
    await cb.answer()


async def _resolve_social(cb: CallbackQuery, db: AsyncSession, db_user: User, *, approved: bool) -> None:
    from app.models.social import SocialProof

    raw = cb.data.split(":", 1)[-1]
    try:
        proof_id = UUID(raw)
    except ValueError:
        await cb.answer("نامعتبر", show_alert=True)
        return
    proof = await db.get(SocialProof, proof_id)
    if not proof:
        await cb.answer("یافت نشد", show_alert=True)
        return
    event = await db.scalar(
        select(Event).where(Event.id == proof.event_id).options(*event_public_load_options())
    )
    if not event:
        await cb.answer("کاستوم یافت نشد", show_alert=True)
        return
    allowed = bool(event.organizer and event.organizer.user_id == db_user.id)
    if not allowed:
            allowed = await is_active_admin(db, db_user)
    if not allowed:
        await cb.answer("این اسکرین برای کاستوم شما نیست.", show_alert=True)
        return
    if proof.status != SocialProofStatus.PENDING:
        await cb.answer("قبلاً بررسی شده است.", show_alert=True)
        return
    await review_proof(db, proof, approved=approved, reviewer_id=db_user.id)
    player = await db.get(User, proof.user_id)
    confirmed = False
    if approved and player:
        try:
            result = await register_user(
                db, user=player, event=event, bot=cb.bot, source="social", accept_rules=True
            )
            confirmed = result.registration.status == RegistrationStatus.CONFIRMED
        except AppError:
            confirmed = True  # already registered
        except Exception:  # noqa: BLE001
            confirmed = False
    await db.commit()
    if player and not player.is_bot_blocked:
        if approved:
            note = (
                "✅ اسکرین فالو شما تأیید شد و ثبت‌نامتان در کاستوم "
                f"«{esc(_short_label(event))}» قطعی شد.\n"
                "سر ساعت ROOM ID و PASS برایتان می‌آید — فقط تا آن لحظه در کانال‌ها بمانید."
                if confirmed
                else "✅ اسکرین فالو شما تأیید شد. برای قطعی شدن ثبت‌نام، دکمهٔ «عضو شدم» را در کارت کاستوم بزنید."
            )
        else:
            note = (
                f"❌ اسکرین فالو شما برای کاستوم «{esc(_short_label(event))}» تأیید نشد.\n"
                "دوباره از کارت کاستوم اسکرین درست را بفرستید."
            )
        try:
            await cb.bot.send_message(player.telegram_id, note)
        except Exception:  # noqa: BLE001
            pass
    await cb.answer("تأیید شد" if approved else "رد شد")
    await cb.message.answer("✅ ثبت‌نام این بازیکن قطعی شد." if approved else "❌ رد شد.")


@router.callback_query(F.data.startswith("socok:"))
async def org_social_ok(cb: CallbackQuery, db: AsyncSession, db_user: User):
    await _resolve_social(cb, db, db_user, approved=True)


@router.callback_query(F.data.startswith("socno:"))
async def org_social_no(cb: CallbackQuery, db: AsyncSession, db_user: User):
    await _resolve_social(cb, db, db_user, approved=False)


# ---------------------------------------------------------------- winners


@router.callback_query(F.data == "orgp:win")
async def org_winners(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if await _blocked_organize(db, db_user, cb):
        return
    org = await db.scalar(select(Organizer).where(Organizer.user_id == db_user.id))
    if not org:
        await cb.answer("اول یک کاستوم بسازید.", show_alert=True)
        return
    rows = await claims_for_organizer(db, org.id, limit=10)
    if not rows:
        await cb.message.answer(
            "هنوز کسی برای کاستوم‌های شما ادعای برنده ثبت نکرده.\n"
            "بعد از شروع کاستوم، بازیکن‌ها از دکمهٔ «برنده» اسکرین می‌فرستند و همین‌جا می‌بینید.",
            reply_markup=organizer_home_kb(),
        )
        await cb.answer()
        return
    flags = {"pending": "⏳ در انتظار شما", "approved": "✅ تأیید شده", "rejected": "❌ رد شده"}
    await cb.message.answer(
        "🏆 <b>برنده‌ها و تحویل جایزه</b>\n"
        "اسکرین هر بازیکن و آیدی‌اش را می‌بینید. با «تأیید برنده» آیدی دریافت جایزه برایش ارسال می‌شود، "
        "و از «پیام به برنده» می‌توانید همین‌جا با او حرف بزنید."
    )
    for claim in rows:
        event = claim.event
        caption = (
            f"{flags.get(claim.status, '•')}\n"
            f"کاستوم: {esc(_short_label(event)) if event else '—'}\n"
            f"بازیکن: {format_person(claim.user)}\n"
        )
        kb = winner_claim_review_kb(
            str(claim.id),
            approved=claim.status != "pending",
            player_url=player_dm_link(claim.user),
        )
        try:
            await cb.message.answer_photo(claim.screenshot_file_id, caption=caption[:1024], reply_markup=kb)
        except Exception:  # noqa: BLE001
            await cb.message.answer(caption + "\n<i>اسکرین قابل نمایش نیست.</i>", reply_markup=kb)
    await cb.message.answer("بازگشت به پنل:", reply_markup=organizer_home_kb())
    await cb.answer()


async def _claim_for_organizer(db: AsyncSession, db_user: User, raw: str):
    from app.models.winner import WinnerClaim

    try:
        claim_id = UUID(raw)
    except ValueError:
        return None, None
    claim = await db.scalar(
        select(WinnerClaim)
        .where(WinnerClaim.id == claim_id)
        .options(selectinload(WinnerClaim.event), selectinload(WinnerClaim.user))
    )
    if not claim:
        return None, None
    event = claim.event
    if event is None:
        return None, None
    org = await db.get(Organizer, claim.organizer_id) if claim.organizer_id else None
    if org and org.user_id == db_user.id:
        return claim, event
    if await is_active_admin(db, db_user):
        return claim, event
    return None, None


async def _resolve_claim_cb(cb: CallbackQuery, db: AsyncSession, db_user: User, *, approved: bool) -> None:
    claim, event = await _claim_for_organizer(db, db_user, cb.data.split(":")[-1])
    if not claim:
        await cb.answer("این ادعا برای کاستوم شما نیست.", show_alert=True)
        return
    if claim.status != WinnerClaimStatus.PENDING:
        await cb.answer("قبلاً بررسی شده است.", show_alert=True)
        return
    await resolve_claim(db, claim, approved=approved, reviewer_id=db_user.id)
    contact = await resolve_payout_contact(db, event) if approved else None
    if approved and event.organizer_id:
        org = await db.get(Organizer, event.organizer_id)
        if org:
            from app.services import trust as trust_svc

            await trust_svc.record(
                db, org, "prize_paid_confirmed", related_event_id=event.id, actor_id=db_user.id
            )
    await db.commit()
    winner, _ = await claim_parties(db, claim)
    if winner and not winner.is_bot_blocked:
        text = (
            format_payout_note(event, contact)
            if approved
            else (
                f"ادعای برنده بودن شما در کاستوم «{esc(_short_label(event))}» تأیید نشد.\n"
                "اگر فکر می‌کنید اشتباه شده، از «پاسخ به برگزارکننده» توضیح بدهید."
            )
        )
        try:
            await cb.bot.send_message(
                winner.telegram_id,
                text,
                reply_markup=winner_reply_kb(str(claim.id), contact_url=contact_link(contact)),
            )
        except Exception:  # noqa: BLE001
            pass
    await cb.answer("ثبت شد")
    if approved:
        shown = esc(contact) if contact else "—"
        await cb.message.answer(
            f"✅ برنده تأیید شد و آیدی <b>{shown}</b> برایش ارسال شد.\n"
            "اگر آیدی درست نیست، از «آیدی دریافت جایزه» در پنل عوضش کنید.",
            reply_markup=organizer_home_kb(),
        )
    else:
        await cb.message.answer("❌ رد شد و به بازیکن اطلاع داده شد.", reply_markup=organizer_home_kb())


@router.callback_query(F.data.startswith("orgw:ok:"))
async def org_winner_ok(cb: CallbackQuery, db: AsyncSession, db_user: User):
    await _resolve_claim_cb(cb, db, db_user, approved=True)


@router.callback_query(F.data.startswith("orgw:no:"))
async def org_winner_no(cb: CallbackQuery, db: AsyncSession, db_user: User):
    await _resolve_claim_cb(cb, db, db_user, approved=False)


@router.callback_query(F.data.startswith("orgw:msg:"))
async def org_winner_message(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    claim, event = await _claim_for_organizer(db, db_user, cb.data.split(":")[-1])
    if not claim:
        await cb.answer("این ادعا برای کاستوم شما نیست.", show_alert=True)
        return
    await state.set_state(WinnerChatSG.to_winner)
    await state.update_data(claim_id=str(claim.id))
    await cb.message.answer(
        f"✉️ پیام شما برای <b>{format_person(claim.user)}</b> فرستاده می‌شود.\n"
        "متن را همین‌جا بنویسید. او می‌تواند از داخل ربات جواب بدهد.",
        reply_markup=wizard_nav(),
    )
    await cb.answer()


@router.message(WinnerChatSG.to_winner, ~F.text.in_(MENU_BUTTON_TEXTS))
async def org_winner_message_body(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    body = (message.text or "").strip()
    if not body:
        await message.answer("متن پیام را بنویسید، یا «لغو» را بزنید.", reply_markup=wizard_nav())
        return
    if len(body) > 1000:
        await message.answer("پیام حداکثر ۱۰۰۰ حرف باشد.", reply_markup=wizard_nav())
        return
    data = await state.get_data()
    claim, event = await _claim_for_organizer(db, db_user, data.get("claim_id") or "")
    if not claim or not event:
        await state.clear()
        await message.answer("این گفت‌وگو دیگر در دسترس نیست.", reply_markup=organizer_home_kb())
        return
    winner, _ = await claim_parties(db, claim)
    delivered = False
    if winner and not winner.is_bot_blocked:
        contact = await resolve_payout_contact(db, event)
        try:
            await message.bot.send_message(
                winner.telegram_id,
                format_relayed_to_winner(event, body),
                reply_markup=winner_reply_kb(str(claim.id), contact_url=contact_link(contact)),
            )
            delivered = True
        except Exception:  # noqa: BLE001
            delivered = False
    await record_message(db, claim=claim, sender_id=db_user.id, body=body, delivered=delivered)
    await db.commit()
    await state.clear()
    await message.answer(
        "✉️ پیام برای برنده ارسال شد." if delivered else "پیام ثبت شد ولی به بازیکن نرسید (ربات را بلاک کرده).",
        reply_markup=organizer_home_kb(),
    )


# ---------------------------------------------------------------- payout contact


@router.callback_query(F.data == "orgp:payout")
async def org_payout_settings(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    if await _blocked_organize(db, db_user, cb):
        return
    if await _organizer_ready(db, db_user, cb.message) is None:
        await cb.answer()
        return
    org = await db.scalar(select(Organizer).where(Organizer.user_id == db_user.id))
    saved = (org.payout_contact or "").strip() if org else ""
    current = f"آیدی فعلی: <b>{esc(saved)}</b>" if saved else "هنوز آیدی ثبت نکرده‌اید."
    await state.set_state(OrganizerSettingsSG.payout_contact)
    await cb.message.answer(
        "🏆 <b>آیدی دریافت جایزه</b>\n"
        f"{current}\n\n"
        "وقتی برنده‌ای را تأیید می‌کنید، ربات همین آیدی را برایش می‌فرستد تا به پی‌وی شما بیاید.\n"
        "آیدی جدید را بفرستید. نمونه: <code>@my_id</code>",
        reply_markup=payout_contact_kb(saved=saved or None, username=db_user.username),
    )
    await cb.answer()


@router.message(OrganizerSettingsSG.payout_contact, ~F.text.in_(MENU_BUTTON_TEXTS))
async def org_payout_save(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    try:
        contact = normalize_payout_contact(message.text or "")
    except AppError as exc:
        await message.answer(exc.message, reply_markup=wizard_nav())
        return
    org = await db.scalar(select(Organizer).where(Organizer.user_id == db_user.id))
    if org:
        org.payout_contact = contact
        await db.commit()
    await state.clear()
    await message.answer(
        f"✅ آیدی دریافت جایزه ثبت شد: <b>{esc(contact)}</b>",
        reply_markup=organizer_home_kb(),
    )


@router.callback_query(OrganizerSettingsSG.payout_contact, F.data.in_({"payc:saved", "payc:self"}))
async def org_payout_shortcut(cb: CallbackQuery, state: FSMContext, db: AsyncSession, db_user: User):
    org = await db.scalar(select(Organizer).where(Organizer.user_id == db_user.id))
    if cb.data == "payc:self" and db_user.username:
        contact = f"@{db_user.username.lstrip('@')}"
    else:
        contact = (org.payout_contact or "").strip() if org else ""
    if not contact:
        await cb.answer("آیدی ذخیره‌شده‌ای نیست. خودتان بنویسید.", show_alert=True)
        return
    if org:
        org.payout_contact = contact
        await db.commit()
    await state.clear()
    await cb.message.answer(
        f"✅ آیدی دریافت جایزه ثبت شد: <b>{esc(contact)}</b>",
        reply_markup=organizer_home_kb(),
    )
    await cb.answer("ثبت شد")
