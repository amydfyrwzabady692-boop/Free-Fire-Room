from __future__ import annotations

from datetime import UTC, date, datetime as dt, timedelta
from uuid import UUID

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.access import menu_for
from app.bot.helpers import esc, replace_callback_view
from app.bot.keyboards.common import (
    DANGER,
    PRIMARY,
    SUCCESS,
    announcement_list_kb,
    ibtn,
    labeled,
    pick_date_kb,
    wizard_nav,
)
from app.bot.onboarding import ensure_onboarding, target_message
from app.bot.states.groups import AnnounceSG
from app.core.enums import BanScope
from app.core.errors import AppError
from app.core.time import combine_local_date_and_clock, format_jalali_date, format_local, parse_clock, upcoming_local_dates
from app.models.announcement import CustomAnnouncement
from app.models.user import User
from app.services.announcements import (
    channel_from_link,
    create_announcement,
    delete_own_announcement,
    list_upcoming_announcements,
)
from app.services.bans import is_banned
from app.services.events import MIN_START_LEAD_MINUTES

router = Router(name="announce")

NEWS_NOTE = (
    "این فقط اطلاع‌رسانی است. ROOM ID و PASS داخل ربات نمی‌آید "
    "مگر خود برگزارکننده کاستوم را از «ثبت کاستوم» ثبت کرده باشد."
)


def _ann_url(row: CustomAnnouncement) -> str | None:
    if row.channel_url:
        return row.channel_url
    if row.channel_username:
        return f"https://t.me/{row.channel_username.lstrip('@')}"
    return None


def _card(row: CustomAnnouncement) -> str:
    when = format_local(row.starts_at, row.timezone)
    channel = row.channel_name or row.channel_url or "کانال کاستوم"
    return (
        f"خبر کاستوم\n"
        f"کانال: {esc(channel)}\n"
        f"ساعت (شمسی): {when}\n\n"
        f"{NEWS_NOTE}"
    )


def _ann_kb(row: CustomAnnouncement, *, owner: bool = False) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    url = _ann_url(row)
    if url:
        buttons.append([ibtn("ورود به کانال", url=url, style=PRIMARY)])
    if owner:
        buttons.append([ibtn("حذف اطلاع‌رسانی من", callback_data=f"ann:del:{row.id}", style=DANGER)])
    buttons.append([ibtn("بازگشت", callback_data="ann:list", style=PRIMARY)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn("تأیید و ثبت", callback_data="ann:ok", style=SUCCESS)],
            [ibtn("انصراف", callback_data="wiz:cancel", style=DANGER)],
        ]
    )


@router.message(F.text.in_(labeled("اطلاع‌رسانی", "ثبت اطلاع‌رسانی")))
@router.callback_query(F.data == "ann:list")
async def list_ann(event: Message | CallbackQuery, db: AsyncSession, db_user: User):
    msg = target_message(event)
    if not await ensure_onboarding(msg, db_user, db, recheck_channels=not isinstance(event, CallbackQuery)):
        if isinstance(event, CallbackQuery):
            await event.answer()
        return
    rows = await list_upcoming_announcements(db)
    if not rows:
        text = (
            "اطلاع‌رسانی فعالی نیست.\n"
            "اگر از کاستومی خبر دارید، فقط لینک کانال و ساعت را ثبت کنید. "
            "شرایط جوین و ROOM ID / PASS در این بخش نیست؛ کاربران فقط خبردار می‌شوند."
        )
        kb = announcement_list_kb([])
    else:
        items = [(str(r.id), f"{format_local(r.starts_at, r.timezone)} | {r.channel_name}") for r in rows]
        text = "خبر کاستوم‌ها (فقط لینک کانال و ساعت):"
        kb = announcement_list_kb(items)
    if isinstance(event, CallbackQuery):
        await replace_callback_view(event, text, inline=kb)
        return
    await msg.answer(text, reply_markup=kb)


@router.callback_query(F.data == "ann:new")
async def start_ann(event: Message | CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    msg = target_message(event)
    if not await ensure_onboarding(msg, db_user, db):
        if isinstance(event, CallbackQuery):
            await event.answer()
        return
    if await is_banned(db, db_user, BanScope.ORGANIZE):
        text = "امکان ثبت اطلاع‌رسانی برای شما محدود شده است."
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await msg.answer(text)
        return
    await state.set_state(AnnounceSG.channel_link)
    await msg.answer(
        "فقط لینک یا @username کانال را بفرستید.\n"
        "نمونه: https://t.me/example یا @example\n"
        f"{NEWS_NOTE}\n"
        "برای انصراف /cancel",
        reply_markup=wizard_nav(),
    )
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.message(AnnounceSG.channel_link)
async def ann_link(message: Message, state: FSMContext):
    try:
        parsed = channel_from_link(message.text or "")
    except AppError as exc:
        await message.answer(exc.message)
        return
    await state.update_data(**parsed)
    await state.set_state(AnnounceSG.starts_at)
    await message.answer(
        "ساعت کاستوم را مشخص کنید. اول روز را انتخاب کنید (شمسی، تهران).",
        reply_markup=pick_date_kb("and"),
    )


@router.callback_query(AnnounceSG.starts_at, F.data.startswith("and:"))
async def ann_pick_date(cb: CallbackQuery, state: FSMContext):
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
    await state.set_state(AnnounceSG.starts_time)
    await cb.message.answer(
        f"تاریخ: {format_jalali_date(day)}\nحالا ساعت را بفرستید. نمونه: 22:00 یا 22",
        reply_markup=wizard_nav(),
    )
    await cb.answer()


@router.message(AnnounceSG.starts_at)
async def ann_need_date(message: Message):
    await message.answer("یکی از دکمه‌های امروز / فردا / پس‌فردا را بزنید.", reply_markup=pick_date_kb("and"))


@router.message(AnnounceSG.starts_time)
async def ann_time(message: Message, state: FSMContext):
    data = await state.get_data()
    picked = data.get("picked_date")
    if not picked:
        await state.set_state(AnnounceSG.starts_at)
        await message.answer("اول روز را انتخاب کنید.", reply_markup=pick_date_kb("and"))
        return
    try:
        hour, minute = parse_clock(message.text or "")
        when = combine_local_date_and_clock(date.fromisoformat(picked), hour, minute)
    except ValueError:
        await message.answer("ساعت نامعتبر است. نمونه: 22:00 یا 22")
        return
    if when <= dt.now(UTC) + timedelta(minutes=MIN_START_LEAD_MINUTES):
        await message.answer(f"ساعت باید حداقل {MIN_START_LEAD_MINUTES} دقیقه بعد باشد.")
        return
    await state.update_data(starts_at=when.isoformat())
    await state.set_state(AnnounceSG.preview)
    await message.answer(
        "پیش‌نمایش خبر:\n"
        f"کانال: {esc(data.get('channel_name') or data.get('channel_url'))}\n"
        f"ساعت: {format_local(when)}\n\n"
        f"{NEWS_NOTE}",
        reply_markup=_confirm_kb(),
    )


@router.message(AnnounceSG.preview, ~F.text.in_(labeled("تأیید", "انتشار")))
async def ann_preview_hint(message: Message):
    await message.answer("برای ثبت، دکمه سبز «تأیید و ثبت» را بزنید.", reply_markup=_confirm_kb())


@router.message(AnnounceSG.preview, F.text.in_(labeled("تأیید", "انتشار")))
@router.callback_query(AnnounceSG.preview, F.data == "ann:ok")
async def ann_finish(event: Message | CallbackQuery, state: FSMContext, db: AsyncSession, db_user: User):
    msg = event.message if isinstance(event, CallbackQuery) else event
    data = await state.get_data()
    try:
        row = await create_announcement(
            db,
            db_user,
            {
                **data,
                "starts_at": dt.fromisoformat(data["starts_at"]),
            },
        )
    except AppError as exc:
        await msg.answer(exc.message)
        if isinstance(event, CallbackQuery):
            await event.answer()
        return
    except Exception:
        await msg.answer("اطلاع‌رسانی ناقص است. از اول ثبت کنید.")
        await state.clear()
        if isinstance(event, CallbackQuery):
            await event.answer()
        return
    await state.clear()
    await msg.answer(
        "خبر ثبت شد. کاربران در بخش اطلاع‌رسانی لینک کانال و ساعت را می‌بینند.\n"
        "ROOM ID و PASS از ربات نمی‌رود مگر خودتان از «ثبت کاستوم» کاستوم را ثبت کنید.",
        reply_markup=await menu_for(db, db_user),
    )
    await msg.answer(_card(row), reply_markup=_ann_kb(row, owner=True))
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.callback_query(F.data.startswith("ann:del:"))
async def ann_delete(cb: CallbackQuery, db: AsyncSession, db_user: User):
    row = await db.get(CustomAnnouncement, UUID(cb.data.split(":")[-1]))
    if not row:
        await cb.answer("یافت نشد", show_alert=True)
        return
    try:
        await delete_own_announcement(db, row, db_user)
    except AppError as exc:
        await cb.answer(exc.message, show_alert=True)
        return
    await cb.message.answer("اطلاع‌رسانی حذف شد.")
    await cb.answer()


@router.callback_query(F.data.startswith("annv:"))
async def ann_open(cb: CallbackQuery, db: AsyncSession, db_user: User):
    try:
        row = await db.get(CustomAnnouncement, UUID(cb.data.split(":", 1)[1]))
    except ValueError:
        await cb.answer("نامعتبر", show_alert=True)
        return
    if not row or row.status != "published":
        await cb.answer("این اطلاع‌رسانی در دسترس نیست.", show_alert=True)
        return
    await cb.message.answer(_card(row), reply_markup=_ann_kb(row, owner=row.user_id == db_user.id))
    await cb.answer()
