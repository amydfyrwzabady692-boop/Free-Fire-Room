from __future__ import annotations

from datetime import UTC, date, datetime as dt, timedelta
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardMarkup, Message
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramForbiddenError
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
    CHANNEL_POST_LABEL,
    channel_post_kb,
    event_share_kb,
    ibtn,
    labeled,
    MENU_BUTTON_TEXTS,
    organizer_home_kb,
    payout_contact_kb,
    pick_date_kb,
    organizer_profile_kb,
    post_confirm_kb,
    social_bulk_kb,
    social_filter_row,
    social_reject_all_kb,
    social_review_kb,
    start_confirm_kb,
    winner_claim_review_kb,
    winner_reply_kb,
    wizard_nav,
)
from app.locales.labels import event_status_fa, org_status_fa, reg_status_fa
from app.locales.style import room_pair
from app.bot.onboarding import ensure_onboarding, target_message
from app.bot.paging import DEFAULT_PAGE_SIZE
from app.bot.states.groups import (
    CredsWaitSG,
    EventWizardSG,
    RepeatSG,
    OrganizerSettingsSG,
    WinnerChatSG,
)
from app.core.config import get_settings
from app.core.enums import (
    BanScope,
    DeliveryStatus,
    EventStatus,
    OrganizerStatus,
    RegistrationStatus,
    SocialProofStatus,
    WinnerClaimStatus,
)
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.time import combine_local_date_and_clock, format_jalali_date, format_local, parse_clock, upcoming_local_dates
from app.models.channel import Channel, ChannelOwnership
from app.models.event import Event, RoomCredential
from app.models.jobs import Delivery
from app.models.organizer import Organizer
from app.models.registration import Registration
from app.models.user import User
from app.services.bans import is_banned
from app.services.channels import connect_organizer_channel, list_owned_channels
from app.services.audit import write_audit
from app.services.credentials import queue_late_credentials
from app.services.event_display import (
    channel_public_label,
    default_custom_description,
    event_prize_text,
    event_public_load_options,
    format_channel_post_caption,
    format_event_identity_block,
    format_event_list_label,
    resolve_event_channel,
)
from app.services.events import (
    cancel_event,
    create_event,
    mark_event_started,
    repeat_event,
    submit_for_publish,
    update_credentials,
    waiting_live_credential_event,
)
from app.services.organizers import get_or_apply
from app.services.posters import as_input_file, event_poster_bytes
from app.services.telegram_ops import inspect_bot_admin, pace as _pace
from app.services.social import (
    MAX_SOCIAL_TASKS,
    PLATFORM_FA,
    list_tasks as list_social_tasks,
    normalize_social_url,
    pending_proof_count,
    pending_proofs_for_event,
    proof_counts_for_event,
    proof_status_label,
    proofs_for_event,
    review_proof,
    social_required,
    task_label,
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
log = get_logger(__name__)

def _short_label(e: Event, limit: int = 50) -> str:
    return ((e.prize_summary or e.title or "کاستوم").strip())[:limit]


def _person_short(user: User | None, limit: int = 22) -> str:
    """A name that fits on a button next to "دیدن اسکرین N"."""
    if user is None:
        return "بازیکن"
    name = (user.first_name or "").strip() or (user.username or "").strip()
    return (name or str(user.telegram_id))[:limit]


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
    choices = upcoming_local_dates(5)
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
    max_ch = int(await get_setting(db, "max_required_channels_per_event", 8))
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
    max_ch = int(await get_setting(db, "max_required_channels_per_event", 8))
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
    max_ch = int(await get_setting(db, "max_required_channels_per_event", 8))
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
    "این متن هم روی دکمهٔ کاستوم در فهرست دیده می‌شود، هم داخل کارت آن، "
    "و هم روی بنری که در کانال می‌گذارید.\n\n"
    "💡 <b>هر خط یک نفر.</b> اگر برای نفر دوم و سوم هم جایزه دارید، هرکدام را "
    "در یک خط جدا بنویسید تا روی بنر «نفر اول / نفر دوم / نفر سوم» بشود.\n\n"
    "نمونهٔ یک‌خطی:\n"
    "<code>واریز یک میلیون تومان به کارت</code>\n\n"
    "نمونهٔ چندخطی:\n"
    "<code>واریز یک میلیون به کارت\n۵۰۰ هزار تومان\n۱۰۰۰ الماس</code>"
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
    f"می‌توانید تا {MAX_SOCIAL_TASKS} پیج بدهید — بعد از هر آدرس، آدرس بعدی را بفرستید "
    "و آخرش «تمام شد» را بزنید.\n\n"
    "بازیکن برای <b>هر پیج یک اسکرین جدا</b> می‌فرستد؛ اسکرین‌ها برای شما می‌آید و تا "
    "تأیید نکنید ثبت‌نامش قطعی نمی‌شود.\n\n"
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
    await state.update_data(social_pages=[])
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


def _social_pages(data: dict) -> list[dict]:
    return list(data.get("social_pages") or [])


def _social_done_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn("تمام شد — ادامه", callback_data="socdone", style=SUCCESS)],
            [ibtn("لغو", callback_data="wiz:cancel", style=DANGER)],
        ]
    )


async def _finish_social_step(message: Message, state: FSMContext) -> None:
    pages = _social_pages(await state.get_data())
    if pages:
        first = pages[0]
        await state.update_data(social_url=first["url"], social_platform=first["platform"])
    else:
        await state.update_data(social_url=None, social_platform=None)
    await _ask_description(message, state)


@router.message(EventWizardSG.social)
async def wiz_social(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    text = (message.text or "").strip()
    data = await state.get_data()
    pages = _social_pages(data)
    if text in labeled("-", "رد کردن", "رد", "ندارم", "تمام شد", "ادامه"):
        await _finish_social_step(message, state)
        return
    if len(pages) >= MAX_SOCIAL_TASKS:
        await message.answer(
            f"سقف {MAX_SOCIAL_TASKS} پیج است. «تمام شد» را بزنید.",
            reply_markup=_social_done_kb(),
        )
        return
    try:
        url, platform = normalize_social_url(text)
    except AppError as exc:
        await message.answer(exc.message, reply_markup=wizard_nav(include_skip=True, include_back=True))
        return
    if any(p["url"] == url for p in pages):
        await message.answer("این پیج را قبلاً اضافه کرده‌اید.", reply_markup=_social_done_kb())
        return
    pages.append({"url": url, "platform": platform})
    await state.update_data(social_pages=pages)
    listing = "\n".join(
        f"{i}) {PLATFORM_FA.get(p['platform'], 'پیج')} — {esc(p['url'])}"
        for i, p in enumerate(pages, start=1)
    )
    await message.answer(
        f"✅ ثبت شد ({len(pages)}/{MAX_SOCIAL_TASKS}):\n{listing}\n\n"
        "پیج بعدی را بفرستید، یا «تمام شد» را بزنید.",
        reply_markup=_social_done_kb(),
    )


@router.callback_query(EventWizardSG.social, F.data == "socdone")
async def wiz_social_done(cb: CallbackQuery, state: FSMContext, db: AsyncSession, db_user: User):
    await _finish_social_step(cb.message, state)
    await cb.answer()


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
        await state.update_data(social_pages=[])
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
        await state.update_data(social_pages=[])
        await _finish_social_step(cb.message, state)
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
    fill_end = starts_at + timedelta(minutes=get_settings().auto_archive_minutes)
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
        "social_pages": _social_pages(data),
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
        pages = _social_pages(data)
        social_line = (
            "📸 فالو اجباری: "
            + "، ".join(f"{PLATFORM_FA.get(p['platform'], 'پیج')}" for p in pages)
            + f" ({len(pages)} پیج)\n"
            if pages
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
            "🖼 <b>بنر آماده است:</b> از «انتشار بنر در کانال» بزنید تا ربات خودش پست را "
            f"با دکمهٔ «{CHANNEL_POST_LABEL}» در کانالتان بگذارد.\n\n"
            "🆔 <b>قدم بعدی:</b> از «ارسال ROOM ID / PASS» می‌توانید <b>همین حالا</b> مشخصات اتاق را ثبت کنید؛ "
            "ربات سر ساعت خودکار برای واجدین شرایط می‌فرستد و نتیجه را به شما خبر می‌دهد.\n\n"
            "⏹ <b>مهم:</b> این کاستوم تا وقتی خودتان دکمهٔ «کاستوم شروع شد» را نزنید "
            "در فهرست «کاستوم‌های پیش‌رو» می‌ماند و ثبت‌نام باز است — هر کس در این مدت "
            "شرایط را کامل کند، ROOM ID و PASS خودکار برایش می‌رود.\n"
            "هر وقت بازی را شروع کردید، از «کاستوم‌ها و آمار من» آن دکمه را بزنید تا به «گذشته» برود.\n"
            f"اگر یادتان رفت، {get_settings().auto_archive_minutes} دقیقه بعد از ساعت شروع خودکار بسته می‌شود.\n\n"
            "🔔 ربات یک ساعت قبل و ده دقیقه قبل از شروع، این کاستوم را به کاربران خبر می‌دهد."
        )
        # show the real banner, exactly as the channel would get it, rather
        # than the bare photo with no caption and no button
        try:
            png, caption, deep = await _banner_parts(db, event)
            await _send_banner(message.bot, message.chat.id, event, png, caption, deep)
        except Exception:  # noqa: BLE001
            log.exception("publish_banner_preview_failed", event_id=str(event.id))
        await message.answer(details, reply_markup=event_share_kb(link))
        await message.answer(
            "می‌خواهید همین بنر را در کانالتان بگذارم؟",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        ibtn(
                            "انتشار بنر در کانال",
                            callback_data=f"orgp:post:{event.public_token}",
                            style=SUCCESS,
                        )
                    ]
                ]
            ),
        )
        if event.status == EventStatus.PUBLISHED:
            # everyone hears about it now, not only when a reminder fires
            await db.commit()
            from app.workers.enqueue import spawn
            from app.workers.tasks import announce_new_event

            spawn(announce_new_event, str(event.id))
            await message.answer(
                "کاستوم در فهرست همه قرار گرفت و به کاربران ربات خبر داده شد.",
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
        counts = await proof_counts_for_event(db, e.id) if social_required(e) else {}
        buttons = []
        if can_send:
            buttons.append([ibtn("ارسال ROOM ID / PASS", callback_data=f"orgp:creds:{e.public_token}", style=SUCCESS)])
        # the archive stays reachable after everything is settled - it is a
        # place to look, not a queue that empties
        if counts.get("total"):
            label = f"اسکرین‌های فالو ({counts['total']})"
            if counts.get("pending"):
                label += f" — {counts['pending']} بررسی‌نشده"
            elif counts.get("rejected"):
                label += f" — {counts['rejected']} رد شده"
            buttons.append(
                [ibtn(label, callback_data=f"orgp:soc:{e.public_token}", style=PRIMARY)]
            )
        buttons.append(
            [
                ibtn("لینک اختصاصی", callback_data=f"orgp:link:{e.public_token}", style=PRIMARY),
                ibtn("قیف و آمار", callback_data=f"orgp:fun:{e.public_token}", style=PRIMARY),
            ]
        )
        if e.status not in {EventStatus.CANCELLED, EventStatus.REJECTED}:
            buttons.append(
                [ibtn("انتشار بنر در کانال", callback_data=f"orgp:post:{e.public_token}", style=SUCCESS)]
            )
        buttons.append(
            [
                ibtn("خروجی شرکت‌کننده‌ها", callback_data=f"orgp:csv:{e.public_token}", style=PRIMARY),
                ibtn("تکرار", callback_data=f"orgp:rep:{e.public_token}", style=SUCCESS),
            ]
        )
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
        "⏳ تا وقتی دکمهٔ «کاستوم شروع شد» را نزده‌اید، هم می‌توانید مشخصات را ثبت کنید "
        "و هم هر بازیکن تازه‌ای که شرایط را کامل کند خودکار مشخصات می‌گیرد.\n"
        f"اگر آن دکمه را نزنید، {get_settings().auto_archive_minutes} دقیقه بعد از ساعت شروع خودکار بسته می‌شود.",
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
        "تا وقتی «کاستوم شروع شد» را نزده‌اید، هر بازیکن تازه‌ای هم که شرایط را کامل کند "
        "بلافاصله مشخصات برایش می‌رود.\n\n"
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
        "از «کاستوم‌های پیش‌رو» برداشته شد و حالا در «کاستوم‌های گذشته» دیده می‌شود.\n"
        "ثبت‌نام جدید و ارسال ROOM ID / PASS برای این کاستوم بسته شد.",
        reply_markup=organizer_home_kb(),
    )
    await cb.answer("ثبت شد")


# ---------------------------------------------------------------- follow screenshots


#: which archive filter each callback letter means
SOCIAL_FILTERS = {
    "a": None,
    "r": [SocialProofStatus.REJECTED],
    "p": [SocialProofStatus.PENDING],
}


def _social_caption(event: Event, proof) -> str:
    page = proof.task.url if proof.task else (event.social_url or "—")
    label = task_label(proof.task)
    return (
        "📸 <b>اسکرین فالو</b>\n"
        f"کاستوم: {esc(_short_label(event))}\n"
        f"بازیکن: {format_person(proof.user)}\n"
        f"پیج ({esc(label)}): {esc(page)}\n"
        f"وضعیت: {proof_status_label(proof.status)}\n\n"
        "ثبت‌نام این بازیکن انجام شده و ROOM ID / PASS برایش می‌رود. "
        "فقط اگر این اسکرین بی‌ربط یا جعلی است «رد» را بزنید."
    )


def _social_row(proof) -> str:
    page = proof.task.url if proof.task else "—"
    return (
        f"{format_person(proof.user)} — {esc(task_label(proof.task))}\n"
        f"<code>{esc(page[:60])}</code> · {proof_status_label(proof.status)}"
    )


async def _audit_event(db: AsyncSession, db_user: User, token: str) -> Event | None:
    """The organizer's own custom, or any custom when the caller is an admin."""
    e = await _own_event(db, db_user, token)
    if e:
        return e
    if not await is_active_admin(db, db_user):
        return None
    return await db.scalar(
        select(Event).where(Event.public_token == token).options(*event_public_load_options())
    )


@router.callback_query(F.data.startswith("orgp:soc:"))
async def org_social_queue(cb: CallbackQuery, db: AsyncSession, db_user: User):
    """Browse every screenshot for one custom - not a queue, an archive.

    Nothing here blocks a player: they are registered the moment they send a
    screenshot. This is where the organizer comes to look, and to reject the
    one that turns out to show somebody else's page.
    """
    if await _blocked_organize(db, db_user, cb):
        return
    parts = cb.data.split(":")
    token = parts[2] if len(parts) > 2 else ""
    index = 0
    if len(parts) > 3:
        try:
            index = max(0, int(parts[3]))
        except ValueError:
            index = 0
    kind = parts[4] if len(parts) > 4 and parts[4] in SOCIAL_FILTERS else "a"
    e = await _audit_event(db, db_user, token)
    if not e:
        await cb.answer("یافت نشد", show_alert=True)
        return
    counts = await proof_counts_for_event(db, e.id)
    shown = {"a": counts["total"], "r": counts["rejected"], "p": counts["pending"]}[kind]
    pages = max(1, (shown + DEFAULT_PAGE_SIZE - 1) // DEFAULT_PAGE_SIZE)
    index = min(index, pages - 1)
    rows = await proofs_for_event(
        db,
        e.id,
        statuses=SOCIAL_FILTERS[kind],
        limit=DEFAULT_PAGE_SIZE,
        offset=index * DEFAULT_PAGE_SIZE,
    )
    head = (
        f"📸 <b>اسکرین‌های فالو</b> — {esc(_short_label(e, 40))}\n"
        f"همه: {counts['total']} · بررسی‌نشده: {counts['pending']} · رد شده: {counts['rejected']}\n"
        "برای ارسال ROOM ID / PASS لازم نیست چیزی را تأیید کنید — "
        "این‌جا فقط برای دیدن و رد کردن اسکرین اشتباه است.\n"
        "——————————————\n"
    )
    if not rows:
        head += (
            "هنوز اسکرینی ثبت نشده است."
            if not counts["total"]
            else "در این دسته چیزی نیست."
        )
    else:
        first = index * DEFAULT_PAGE_SIZE + 1
        head += f"({first} تا {first + len(rows) - 1} از {shown})\n\n"
        head += "\n\n".join(_social_row(p) for p in rows)
    buttons = [
        [
            ibtn(
                f"دیدن اسکرین {i + 1}: {_person_short(p.user)}",
                callback_data=f"socv:{p.id.hex}",
                style=PRIMARY,
            )
        ]
        for i, p in enumerate(rows)
    ]
    buttons.append(social_filter_row(e.public_token, counts, kind))
    nav = []
    if index > 0:
        nav.append(
            ibtn("قبلی", callback_data=f"orgp:soc:{token}:{index - 1}:{kind}", style=PRIMARY)
        )
    if index < pages - 1:
        nav.append(
            ibtn("بعدی", callback_data=f"orgp:soc:{token}:{index + 1}:{kind}", style=PRIMARY)
        )
    if nav:
        buttons.append(nav)
    for row in social_bulk_kb(e.public_token, counts["pending"]).inline_keyboard:
        buttons.append(row)
    await replace_callback_view(cb, head, inline=InlineKeyboardMarkup(inline_keyboard=buttons))
    await cb.answer()


@router.callback_query(F.data.startswith("socv:"))
async def org_social_view(cb: CallbackQuery, db: AsyncSession, db_user: User):
    """One screenshot, full size, with the reject button under it."""
    from app.models.social import SocialProof

    raw = cb.data.split(":", 1)[-1]
    try:
        proof_id = UUID(hex=raw)
    except ValueError:
        await cb.answer("نامعتبر", show_alert=True)
        return
    proof = await db.scalar(
        select(SocialProof)
        .where(SocialProof.id == proof_id)
        .options(selectinload(SocialProof.user), selectinload(SocialProof.task))
    )
    if not proof:
        await cb.answer("یافت نشد", show_alert=True)
        return
    event = await db.scalar(
        select(Event).where(Event.id == proof.event_id).options(*event_public_load_options())
    )
    if not event or not await _may_review_proof(db, db_user, event):
        await cb.answer("این اسکرین برای کاستوم شما نیست.", show_alert=True)
        return
    caption = _social_caption(event, proof)
    kb = social_review_kb(
        str(proof.id), status=proof.status, back=f"orgp:soc:{event.public_token}"
    )
    try:
        await cb.message.answer_photo(proof.file_id, caption=caption[:1024], reply_markup=kb)
    except Exception:  # noqa: BLE001
        await cb.message.answer(
            caption + "\n\n<i>اسکرین قابل نمایش نیست.</i>", reply_markup=kb
        )
    await cb.answer()


async def _may_review_proof(db: AsyncSession, db_user: User, event: Event) -> bool:
    if event.organizer and event.organizer.user_id == db_user.id:
        return True
    return await is_active_admin(db, db_user)


async def _void_registration(db: AsyncSession, event: Event, player: User) -> bool:
    """Take back a seat won with a screenshot that turned out to be junk.

    Returns True when the player was actually holding a confirmed seat. The
    decrement is guarded the same way ``deliver_one`` guards its own demotion,
    so a double tap cannot drive ``confirmed_count`` negative.
    """
    reg = await db.scalar(
        select(Registration).where(
            Registration.event_id == event.id, Registration.user_id == player.id
        )
    )
    if not reg or reg.status != RegistrationStatus.CONFIRMED:
        return False
    reg.status = RegistrationStatus.INELIGIBLE
    reg.ineligible_reason = "social_rejected"
    if event.confirmed_count > 0:
        event.confirmed_count -= 1
    if event.status == EventStatus.FULL:
        event.status = EventStatus.PUBLISHED
    await db.flush()
    return True


async def _creds_already_sent(db: AsyncSession, event: Event, player: User) -> bool:
    row = await db.scalar(
        select(Delivery).where(
            Delivery.event_id == event.id,
            Delivery.user_id == player.id,
            Delivery.kind == "room_credentials",
            Delivery.status == DeliveryStatus.SENT,
        )
    )
    return row is not None


def _reject_note(event: Event, *, had_creds: bool) -> str:
    note = (
        f"❌ اسکرین فالو شما برای کاستوم «{esc(_short_label(event))}» رد شد.\n"
        "برگزارکننده گفته این اسکرین درست نیست."
    )
    if had_creds:
        note += (
            "\n\n⚠️ ورود شما به این کاستوم <b>باطل شد</b> و ROOM ID / PASS "
            "قبلی به کارتان نمی‌آید."
        )
    note += "\n\nاسکرین درست را از کارت کاستوم دوباره بفرستید تا ثبت‌نامتان برگردد."
    return note


async def _resolve_social(cb: CallbackQuery, db: AsyncSession, db_user: User, *, approved: bool) -> None:
    """Reject a screenshot, or undo a rejection that was a mistake.

    There is nothing to approve any more - the registration was final when the
    screenshot arrived. What this does is take a seat back, and give it back.
    """
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
    if not await _may_review_proof(db, db_user, event):
        await cb.answer("این اسکرین برای کاستوم شما نیست.", show_alert=True)
        return
    target = SocialProofStatus.APPROVED if approved else SocialProofStatus.REJECTED
    if proof.status == target:
        await cb.answer("قبلاً همین‌طور بود.", show_alert=True)
        return
    await review_proof(db, proof, approved=approved, reviewer_id=db_user.id)
    player = await db.get(User, proof.user_id)
    had_creds = False
    confirmed = False
    if player is not None:
        if approved:
            # the rejection is lifted: they qualify again, so put the room back
            # on its way instead of leaving them to the next sweep
            try:
                result = await register_user(
                    db, user=player, event=event, bot=cb.bot, source="social", accept_rules=True
                )
                confirmed = result.registration.status == RegistrationStatus.CONFIRMED
            except AppError:
                confirmed = True  # already registered
            except Exception:  # noqa: BLE001
                log.exception("social_undo_register_failed", user_id=str(player.id))
        else:
            had_creds = await _creds_already_sent(db, event, player)
            await _void_registration(db, event, player)
    if not (confirmed and await queue_late_credentials(db, event)):
        await db.commit()
    if player and not player.is_bot_blocked:
        if approved:
            note = (
                f"✅ اسکرین فالو شما در کاستوم «{esc(_short_label(event))}» قبول شد و "
                "ثبت‌نامتان برگشت.\nسر ساعت ROOM ID و PASS برایتان می‌آید — تا آن لحظه در کانال‌ها بمانید."
            )
        else:
            note = _reject_note(event, had_creds=had_creds)
        try:
            await cb.bot.send_message(player.telegram_id, note)
        except Exception:  # noqa: BLE001
            pass
    await cb.answer("برگشت داده شد" if approved else "رد شد")
    await cb.message.answer(
        "✅ ثبت‌نام این بازیکن برگشت."
        if approved
        else "❌ رد شد. تا اسکرین درست نفرستد ROOM ID / PASS برایش نمی‌رود."
    )


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


# ---------------------------------------------------------------- bulk follow review


#: how many screenshots one bulk tap may touch. A reject loop has to DM every
#: player it demotes, and Telegram will not take hundreds of messages from one
#: callback - the leftovers stay for the next tap.
BULK_SOCIAL_LIMIT = 60


@router.callback_query(F.data.startswith("socall:ok:"))
async def org_social_all_ok(cb: CallbackQuery, db: AsyncSession, db_user: User):
    """Mark the unreviewed ones as looked-at. Changes nothing about eligibility.

    This is the honest bulk action now: those players are already registered
    and already getting the room, so all this clears is the badge.
    """
    if await _blocked_organize(db, db_user, cb):
        return
    token = cb.data.split(":", 2)[-1]
    e = await _audit_event(db, db_user, token)
    if not e:
        await cb.answer("یافت نشد", show_alert=True)
        return
    rows = await pending_proofs_for_event(db, e.id, limit=BULK_SOCIAL_LIMIT)
    if not rows:
        await cb.answer("چیزی برای علامت زدن نیست.", show_alert=True)
        return
    for proof in rows:
        await review_proof(db, proof, approved=True, reviewer_id=db_user.id)
    await db.commit()
    left = await pending_proof_count(db, e.id)
    tail = f"\n{left} تای دیگر مانده — دوباره بزنید." if left else ""
    await cb.answer("انجام شد")
    await cb.message.answer(
        f"✅ {len(rows)} اسکرین «بررسی‌شده» علامت خورد.{tail}\n"
        "چیزی برای بازیکن‌ها عوض نشد — آن‌ها از قبل ثبت‌نام بودند.",
        reply_markup=organizer_home_kb(),
    )


@router.callback_query(F.data.startswith("socall:ask:"))
async def org_social_all_ask(cb: CallbackQuery, db: AsyncSession, db_user: User):
    """Rejecting everyone voids a whole custom, so it never fires from one tap."""
    if await _blocked_organize(db, db_user, cb):
        return
    token = cb.data.split(":", 2)[-1]
    e = await _audit_event(db, db_user, token)
    if not e:
        await cb.answer("یافت نشد", show_alert=True)
        return
    counts = await proof_counts_for_event(db, e.id)
    live = counts["total"] - counts["rejected"]
    if live <= 0:
        await cb.answer("همه از قبل رد شده‌اند.", show_alert=True)
        return
    await cb.message.answer(
        f"⚠️ <b>رد کردن همهٔ اسکرین‌ها</b>\n"
        f"کاستوم: {esc(_short_label(e, 40))}\n\n"
        f"با این کار ثبت‌نام <b>{live} نفر</b> باطل می‌شود و تا اسکرین درست نفرستند "
        "ROOM ID / PASS برایشان نمی‌رود. به همه هم خبر داده می‌شود.\n\n"
        "مطمئنید؟",
        reply_markup=social_reject_all_kb(e.public_token, live),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("socall:no:"))
async def org_social_all_no(cb: CallbackQuery, db: AsyncSession, db_user: User):
    """Void every live screenshot in one custom, after the confirm screen."""
    if await _blocked_organize(db, db_user, cb):
        return
    token = cb.data.split(":", 2)[-1]
    e = await _audit_event(db, db_user, token)
    if not e:
        await cb.answer("یافت نشد", show_alert=True)
        return
    rows = await proofs_for_event(
        db,
        e.id,
        statuses=[SocialProofStatus.PENDING, SocialProofStatus.APPROVED],
        limit=BULK_SOCIAL_LIMIT,
    )
    if not rows:
        await cb.answer("چیزی برای رد کردن نیست.", show_alert=True)
        return
    players: dict = {}
    for proof in rows:
        await review_proof(db, proof, approved=False, reviewer_id=db_user.id)
        players[proof.user_id] = proof.user
    voided = 0
    for player in players.values():
        if player is None:
            continue
        if await _void_registration(db, e, player):
            voided += 1
    await db.commit()
    for player in players.values():
        if not player or player.is_bot_blocked:
            continue
        try:
            await cb.bot.send_message(player.telegram_id, _reject_note(e, had_creds=False))
        except Exception:  # noqa: BLE001
            pass
        await _pace()
    left = await proof_counts_for_event(db, e.id)
    remaining = left["total"] - left["rejected"]
    tail = f"\n{remaining} تای دیگر مانده — دوباره بزنید." if remaining > 0 else ""
    await cb.answer("انجام شد")
    await cb.message.answer(
        f"❌ {len(rows)} اسکرین رد شد و ثبت‌نام {voided} نفر باطل شد.{tail}",
        reply_markup=organizer_home_kb(),
    )


# ---------------------------------------------------------------- start menu


@router.callback_query(F.data == "orgp:startmenu")
async def org_start_menu(cb: CallbackQuery, db: AsyncSession, db_user: User):
    """Pick which custom to declare started, then press the button."""
    if await _blocked_organize(db, db_user, cb):
        return
    if await _organizer_ready(db, db_user, cb.message) is None:
        await cb.answer()
        return
    org = await db.scalar(select(Organizer).where(Organizer.user_id == db_user.id))
    if not org:
        await cb.answer("اول یک کاستوم بسازید.", show_alert=True)
        return
    rows = (
        await db.scalars(
            select(Event)
            .where(
                Event.organizer_id == org.id,
                Event.deleted_at.is_(None),
                Event.archived_at.is_(None),
                Event.status.in_([EventStatus.PUBLISHED, EventStatus.FULL, EventStatus.STARTED]),
            )
            .options(*event_public_load_options())
            .order_by(Event.starts_at.asc())
            .limit(10)
        )
    ).all()
    live = [e for e in rows if not is_archived(e)]
    if not live:
        await cb.message.answer(
            "الان کاستوم بازی ندارید.\n"
            "کاستوم‌هایی که هنوز شروع نشده‌اند اینجا می‌آیند تا با یک دکمه شروع‌شده اعلامشان کنید.",
            reply_markup=organizer_home_kb(),
        )
        await cb.answer()
        return
    buttons = [
        [
            ibtn(
                f"{format_local(e.starts_at, e.timezone, compact=True)} · {_short_label(e, 30)}",
                callback_data=f"orgp:startpick:{e.public_token}",
                style=PRIMARY,
            )
        ]
        for e in live
    ]
    buttons.append([ibtn("بازگشت به پنل", callback_data="orgp:home", style=DANGER)])
    await cb.message.answer(
        "⏹ <b>شروع کاستوم</b>\n"
        "کاستومی که بازی‌اش را شروع کرده‌اید انتخاب کنید.\n\n"
        "تا وقتی این کار را نکنید، کاستوم در «پیش‌رو» می‌ماند و هر بازیکن تازه‌ای که "
        "شرایط را کامل کند ROOM ID / PASS می‌گیرد.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("orgp:startpick:"))
async def org_start_pick(cb: CallbackQuery, db: AsyncSession, db_user: User):
    """Show what stopping this custom will do, before it is irreversible."""
    token = cb.data.split(":", 2)[-1]
    e = await _own_event(db, db_user, token)
    if not e:
        await cb.answer("یافت نشد", show_alert=True)
        return
    from app.services.reviews import event_audience_stats, format_audience_stats

    stats = await event_audience_stats(db, e.id)
    creds = await db.scalar(select(RoomCredential).where(RoomCredential.event_id == e.id))
    warn = (
        ""
        if creds_were_provided(creds)
        else "\n\n⚠️ هنوز ROOM ID / PASS نفرستاده‌اید. اول مشخصات را بفرستید."
    )
    await cb.message.answer(
        f"{format_event_identity_block(e)}\n"
        f"🕐 {format_local(e.starts_at, e.timezone)}\n"
        f"{format_audience_stats(stats)}\n"
        "━━━━━━━━━━━━━━\n"
        "با زدن دکمهٔ زیر:\n"
        "• این کاستوم از «پیش‌رو» به «گذشته» می‌رود\n"
        "• ثبت‌نام جدید بسته می‌شود\n"
        "• ارسال ROOM ID / PASS تمام می‌شود"
        f"{warn}",
        reply_markup=start_confirm_kb(e.public_token),
    )
    await cb.answer()


# ---------------------------------------------------------------- repeat a custom


REPEAT_TIME_TEXT = (
    "🔁 <b>تکرار کاستوم</b>\n"
    "همهٔ تنظیمات از کاستوم قبلی کپی می‌شود: کانال‌های جوین اجباری، جایزه، پیج‌های فالو، "
    "آیدی دریافت جایزه، توضیح و بنر.\n\n"
    "فقط ساعت را بفرستید. نمونه: <code>22:00</code> یا <code>22</code>"
)


async def _repeat_source(db: AsyncSession, db_user: User, token: str) -> Event | None:
    return await db.scalar(
        select(Event)
        .where(Event.public_token == token, Event.deleted_at.is_(None))
        .options(
            *event_public_load_options(),
            selectinload(Event.required_channels),
            selectinload(Event.prizes),
        )
    )


@router.callback_query(F.data.startswith("orgp:rep:"))
async def org_repeat_start(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    """Most organizers run the same custom every night; this is that night."""
    if await _blocked_organize(db, db_user, cb):
        return
    token = cb.data.split(":", 2)[-1]
    e = await _repeat_source(db, db_user, token)
    if not e or not e.organizer or e.organizer.user_id != db_user.id:
        await cb.answer("یافت نشد", show_alert=True)
        return
    await state.clear()
    await state.set_state(RepeatSG.day)
    await state.update_data(source_token=token)
    n_ch = len([c for c in (e.required_channels or []) if c.is_active])
    pages = await list_social_tasks(db, e.id)
    summary = (
        f"🔁 <b>تکرار «{esc(_short_label(e, 40))}»</b>\n"
        f"🎁 {esc(event_prize_text(e))}\n"
        f"📢 {n_ch} کانال جوین اجباری\n"
    )
    if pages:
        summary += f"📸 {len(pages)} پیج فالو\n"
    if e.payout_contact:
        summary += f"🏆 آیدی جایزه: {esc(e.payout_contact)}\n"
    await cb.message.answer(
        summary + "\nروز کاستوم جدید را انتخاب کنید.",
        reply_markup=pick_date_kb("repd"),
    )
    await cb.answer()


@router.callback_query(RepeatSG.day, F.data.startswith("repd:"))
async def org_repeat_day(cb: CallbackQuery, state: FSMContext):
    try:
        offset = int(cb.data.split(":")[1])
    except (IndexError, ValueError):
        await cb.answer("نامعتبر", show_alert=True)
        return
    choices = upcoming_local_dates(5)
    if offset < 0 or offset >= len(choices):
        await cb.answer("این روز در دسترس نیست.", show_alert=True)
        return
    day = choices[offset]["date"]
    await state.update_data(picked_date=day.isoformat())
    await state.set_state(RepeatSG.time)
    await cb.message.answer(
        f"🕐 تاریخ: {format_jalali_date(day)}\n\n" + REPEAT_TIME_TEXT,
        reply_markup=wizard_nav(),
    )
    await cb.answer()


@router.message(RepeatSG.day)
async def org_repeat_need_day(message: Message):
    await message.answer("یکی از روزهای زیر را انتخاب کنید.", reply_markup=pick_date_kb("repd"))


@router.message(RepeatSG.time, ~F.text.in_(MENU_BUTTON_TEXTS))
async def org_repeat_time(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    data = await state.get_data()
    picked = data.get("picked_date")
    token = data.get("source_token")
    if not picked or not token:
        await state.clear()
        await message.answer("دوباره از «تکرار» شروع کنید.", reply_markup=organizer_home_kb())
        return
    try:
        hour, minute = parse_clock(message.text or "")
        when = combine_local_date_and_clock(date.fromisoformat(picked), hour, minute)
    except ValueError:
        await message.answer("ساعت نامعتبر است. نمونه: 22:00 یا 22", reply_markup=wizard_nav())
        return
    if when < dt.now(UTC) - timedelta(minutes=1):
        await message.answer(
            "این ساعت گذشته است. ساعتی از الان به بعد بفرستید.", reply_markup=wizard_nav()
        )
        return
    source = await _repeat_source(db, db_user, token)
    if not source or not source.organizer or source.organizer.user_id != db_user.id:
        await state.clear()
        await message.answer("کاستوم اصلی یافت نشد.", reply_markup=organizer_home_kb())
        return
    org = await db.scalar(select(Organizer).where(Organizer.user_id == db_user.id))
    try:
        event = await repeat_event(db, source, org, db_user.id, when)
        await submit_for_publish(db, event, db_user.id)
        await db.commit()
    except AppError as exc:
        await message.answer(exc.message, reply_markup=organizer_home_kb())
        return
    except Exception:  # noqa: BLE001
        log.exception("repeat_event_failed", token=token)
        await db.rollback()
        await message.answer(
            "تکرار کاستوم الان انجام نشد. چند ثانیه بعد دوباره تلاش کنید.",
            reply_markup=organizer_home_kb(),
        )
        return
    await state.clear()
    event = await db.scalar(
        select(Event).where(Event.id == event.id).options(*event_public_load_options())
    )
    link = event_deep_link(event.public_token)
    if event.status == EventStatus.PUBLISHED:
        from app.workers.enqueue import spawn
        from app.workers.tasks import announce_new_event

        spawn(announce_new_event, str(event.id))
    await message.answer(
        f"✅ <b>کاستوم تکرار شد</b>\n"
        f"{format_event_identity_block(event)}\n"
        f"🕐 {format_local(event.starts_at, event.timezone)}\n\n"
        f"<b>لینک این کاستوم:</b>\n{link}\n\n"
        "🆔 ROOM ID / PASS این کاستوم را جدا بفرستید — از کاستوم قبلی کپی نمی‌شود.",
        reply_markup=event_share_kb(link),
    )
    await message.answer("بازگشت به پنل:", reply_markup=organizer_home_kb())


# ---------------------------------------------------------------- public profile


async def _profile_view(target, db: AsyncSession, org, *, own: bool, back: str) -> None:
    from app.services.organizers import (
        format_organizer_profile,
        organizer_deep_link,
        upcoming_events_for,
    )

    text = await format_organizer_profile(db, org)
    events = await upcoming_events_for(db, org.id)
    link = organizer_deep_link(org.id)
    if own:
        text += (
            "\n\n━━━━━━━━━━━━━━\n"
            "🔗 <b>لینک پروفایل شما</b>\n"
            f"{link}\n"
            "این را در بیوی کانالتان بگذارید تا هر کس بزند، همین کارت و کاستوم‌های بازتان را ببیند."
        )
    elif not events:
        text += "\n\nالان کاستوم بازی ندارد."
    await target(
        text,
        organizer_profile_kb(
            str(org.id),
            [(e.public_token, format_event_list_label(e)) for e in events],
            share_link=link if own else None,
            back=back,
        ),
    )


@router.callback_query(F.data == "orgp:me")
async def org_my_profile(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if await _blocked_organize(db, db_user, cb):
        return
    org = await db.scalar(select(Organizer).where(Organizer.user_id == db_user.id))
    if not org:
        await cb.answer("اول یک کاستوم بسازید.", show_alert=True)
        return

    async def _say(text, kb):
        await cb.message.answer(text, reply_markup=kb)

    await _profile_view(_say, db, org, own=True, back="orgp:home")
    await cb.answer()


# ---------------------------------------------------------------- channel banner


async def _banner_parts(db: AsyncSession, event: Event) -> tuple[bytes | None, str, str]:
    """(poster png, caption, deep link) for one custom.

    The organizer's own uploaded photo wins when they gave one - it is their
    art. Otherwise the bot draws the banner itself, which is the whole point of
    the poster renderer.
    """
    pages = await list_social_tasks(db, event.id)
    caption = format_channel_post_caption(event, social_pages=len(pages))
    link = event_deep_link(event.public_token)
    if event.banner_file_id:
        return None, caption, link
    try:
        png = event_poster_bytes(event, social_pages=len(pages))
    except Exception:  # noqa: BLE001
        log.exception("poster_render_failed", event_id=str(event.id))
        png = None
    return png, caption, link


async def _send_banner(bot, chat_id, event: Event, png, caption: str, link: str):
    """One photo + caption + the entry button, wherever it is going."""
    photo = event.banner_file_id or (as_input_file(png) if png else None)
    kb = channel_post_kb(link)
    if photo is None:
        return await bot.send_message(chat_id, caption, reply_markup=kb)
    return await bot.send_photo(chat_id, photo, caption=caption, reply_markup=kb)


@router.callback_query(F.data.startswith("orgp:post:"))
async def org_post_preview(cb: CallbackQuery, db: AsyncSession, db_user: User):
    """Show the organizer exactly what their channel will get, then ask."""
    if await _blocked_organize(db, db_user, cb):
        return
    token = cb.data.split(":", 2)[-1]
    e = await _own_event(db, db_user, token)
    if not e:
        await cb.answer("یافت نشد", show_alert=True)
        return
    png, caption, link = await _banner_parts(db, e)
    try:
        await _send_banner(cb.bot, cb.message.chat.id, e, png, caption, link)
    except Exception:  # noqa: BLE001
        log.exception("banner_preview_failed", event_id=str(e.id))
        await cb.message.answer("ساخت بنر الان انجام نشد. چند ثانیه بعد دوباره بزنید.")
        await cb.answer()
        return
    channel = resolve_event_channel(e)
    if channel is None:
        await cb.message.answer(
            "این بالا همان چیزی است که در کانال منتشر می‌شود.\n\n"
            "⚠️ کانالی به این کاستوم وصل نیست. از «کانال‌های من» ربات را در کانالتان ادمین کنید.",
            reply_markup=organizer_home_kb(),
        )
        await cb.answer()
        return
    await cb.message.answer(
        "این بالا همان چیزی است که در کانال منتشر می‌شود — با دکمهٔ "
        f"«{CHANNEL_POST_LABEL}» که مستقیم بازیکن را داخل ربات می‌آورد.\n\n"
        f"📢 مقصد: <b>{esc(channel_public_label(channel))}</b>",
        reply_markup=post_confirm_kb(e.public_token, channel_public_label(channel)),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("orgp:pub:"))
async def org_post_publish(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if await _blocked_organize(db, db_user, cb):
        return
    token = cb.data.split(":", 2)[-1]
    e = await _own_event(db, db_user, token)
    if not e:
        await cb.answer("یافت نشد", show_alert=True)
        return
    channel = resolve_event_channel(e)
    if channel is None:
        await cb.answer("کانالی به این کاستوم وصل نیست.", show_alert=True)
        return
    # bot_is_admin is written once at connect time and can be hours stale, so
    # ask Telegram again rather than failing in front of the organizer
    try:
        check = await inspect_bot_admin(cb.bot, channel.telegram_chat_id)
    except Exception:  # noqa: BLE001
        check = None
    if check is not None and not check.is_admin:
        await cb.message.answer(
            "⚠️ ربات الان ادمین این کانال نیست، پس نمی‌تواند پست بگذارد.\n"
            "اول ربات را ادمین کنید و دوباره بزنید.",
            reply_markup=organizer_home_kb(),
        )
        await cb.answer()
        return
    png, caption, link = await _banner_parts(db, e)
    try:
        msg = await _send_banner(cb.bot, channel.telegram_chat_id, e, png, caption, link)
    except TelegramForbiddenError:
        await cb.message.answer(
            "⚠️ تلگرام اجازهٔ ارسال در این کانال را نداد. ربات باید ادمین با دسترسی "
            "«ارسال پیام» باشد.",
            reply_markup=organizer_home_kb(),
        )
        await cb.answer()
        return
    except Exception:  # noqa: BLE001
        log.exception("banner_publish_failed", event_id=str(e.id))
        await cb.message.answer(
            "انتشار در کانال انجام نشد. چند ثانیه بعد دوباره تلاش کنید.",
            reply_markup=organizer_home_kb(),
        )
        await cb.answer()
        return
    await write_audit(
        db,
        action="event_banner_posted",
        entity_type="event",
        entity_id=e.id,
        actor_id=db_user.id,
        extra={"channel_id": str(channel.id)},
    )
    await db.commit()
    where = f"https://t.me/{channel.username}/{msg.message_id}" if channel.username else ""
    await cb.answer("منتشر شد")
    await cb.message.answer(
        f"✅ بنر در <b>{esc(channel_public_label(channel))}</b> منتشر شد."
        + (f"\n{where}" if where else ""),
        reply_markup=organizer_home_kb(),
    )
