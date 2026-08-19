from __future__ import annotations

from datetime import UTC, date, datetime as dt, timedelta
from uuid import UUID

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.access import menu_for
from app.bot.helpers import esc, normalize_join_url
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
    create_announcement,
    delete_own_announcement,
    list_upcoming_announcements,
)
from app.services.bans import is_banned
from app.services.events import MIN_START_LEAD_MINUTES

router = Router(name="announce")


def _card(row: CustomAnnouncement) -> str:
    extra = ""
    links = row.extra_join_links or []
    if links:
        extra = "\nکانال‌های پیشنهادی: " + "، ".join(str(x.get("label") or x.get("url")) for x in links[:6])
    return (
        f"<b>{esc(row.title)}</b>\n"
        f"کانال: {esc(row.channel_name)}\n"
        f"زمان (شمسی): {format_local(row.starts_at, row.timezone)}\n"
        f"جایزه: {esc(row.prize_summary or '—')}\n"
        f"{esc(row.description or '')}"
        f"{extra}\n\n"
        "این مورد اطلاع‌رسانی است. مشخصات اتاق را ربات ارسال نمی‌کند مگر برگزارکننده کاستوم رسمی ثبت کرده باشد."
    )


def _ann_kb(row: CustomAnnouncement, *, owner: bool = False) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    url = row.channel_url
    if not url and row.channel_username:
        url = f"https://t.me/{row.channel_username.lstrip('@')}"
    if url:
        buttons.append([ibtn(f"عضویت در {row.channel_name[:24]}", url=url, style=PRIMARY)])
    for item in row.extra_join_links or []:
        if item.get("url"):
            buttons.append(
                [ibtn(f"عضویت در {str(item.get('label') or 'کانال')[:24]}", url=item["url"], style=PRIMARY)]
            )
    if owner:
        buttons.append([ibtn("حذف اطلاع‌رسانی من", callback_data=f"ann:del:{row.id}", style=DANGER)])
    buttons.append([ibtn("بازگشت", callback_data="ann:list", style=PRIMARY)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == "ann:list")
async def list_ann(event: Message | CallbackQuery, db: AsyncSession, db_user: User):
    msg = target_message(event)
    if not await ensure_onboarding(msg, db_user, db):
        if isinstance(event, CallbackQuery):
            await event.answer()
        return
    rows = await list_upcoming_announcements(db)
    if not rows:
        await msg.answer(
            "اطلاع‌رسانی فعالی نیست.\nاگر از کاستوم جایزه‌داری خبر دارید «ثبت اطلاع‌رسانی» را بزنید.",
            reply_markup=announcement_list_kb([]),
        )
        if isinstance(event, CallbackQuery):
            await event.answer()
        return
    items = [(str(r.id), f"{format_local(r.starts_at, r.timezone)} | {r.channel_name}") for r in rows]
    await msg.answer("اطلاع‌رسانی کاستوم‌های جایزه‌دار:", reply_markup=announcement_list_kb(items))
    if isinstance(event, CallbackQuery):
        await event.answer()


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
    await state.set_state(AnnounceSG.channel_name)
    await msg.answer(
        "نام کانال برگزارکننده را بفرستید.\nمثال: FF Diamond Room\nبرای انصراف /cancel",
        reply_markup=wizard_nav(),
    )
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.message(AnnounceSG.channel_name)
async def ann_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("نام کانال خیلی کوتاه است.")
        return
    await state.update_data(channel_name=name, title=f"کاستوم {name}")
    await state.set_state(AnnounceSG.starts_at)
    await message.answer(
        "روز کاستوم را انتخاب کنید (شمسی، تهران).",
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
        await message.answer(
            f"ساعت باید حداقل {MIN_START_LEAD_MINUTES} دقیقه بعد باشد."
        )
        return
    await state.update_data(starts_at=when.isoformat())
    await state.set_state(AnnounceSG.channel_link)
    await message.answer(
        f"زمان: {format_local(when)}\nلینک یا @username کانال را بفرستید. اگر ندارید «-» بفرستید."
    )


@router.message(AnnounceSG.channel_link)
async def ann_link(message: Message, state: FSMContext):
    parsed = normalize_join_url(message.text or "")
    if parsed:
        label, url = parsed
        username = label if not url.startswith("http") or "t.me/" in url else None
        if url.startswith("https://t.me/"):
            username = url.rsplit("/", 1)[-1]
        await state.update_data(channel_url=url, channel_username=username)
    await state.set_state(AnnounceSG.prize)
    await message.answer("خلاصه جایزه را بفرستید یا «-» برای رد.")


@router.message(AnnounceSG.prize)
async def ann_prize(message: Message, state: FSMContext):
    text = None if (message.text or "").strip() == "-" else (message.text or "").strip()
    await state.update_data(prize_summary=text, extra_join_links=[])
    await state.set_state(AnnounceSG.extra_links)
    await message.answer(
        "اگر کانال دیگری هم باید جوین شوند لینک یا @username را بفرستید.\n"
        "چند مورد پشت‌سرهم بفرستید. وقتی تمام شد «-» بفرستید."
    )


@router.message(AnnounceSG.extra_links)
async def ann_extra(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    data = await state.get_data()
    links = list(data.get("extra_join_links") or [])
    if text != "-":
        parsed = normalize_join_url(text)
        if not parsed:
            await message.answer("لینک نامعتبر است. @username یا لینک تلگرام بفرستید یا «-».")
            return
        label, url = parsed
        links.append({"label": label, "url": url})
        await state.update_data(extra_join_links=links)
        await message.answer(f"اضافه شد ({len(links)}). مورد بعدی یا «-».")
        return
    await state.set_state(AnnounceSG.preview)
    await message.answer(
        "پیش‌نمایش:\n"
        f"کانال: {data.get('channel_name')}\n"
        f"زمان: {format_local(dt.fromisoformat(data['starts_at']))}\n"
        f"جایزه: {data.get('prize_summary') or '—'}\n"
        f"لینک‌های جوین: {len(links) + (1 if data.get('channel_url') else 0)}\n\n"
        "اگر درست است تأیید کنید.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [ibtn("تأیید و ثبت", callback_data="ann:ok", style=SUCCESS)],
                [ibtn("انصراف", callback_data="wiz:cancel", style=DANGER)],
            ]
        ),
    )


@router.message(AnnounceSG.preview, ~F.text.in_(labeled("تأیید", "انتشار")))
async def ann_preview_hint(message: Message):
    await message.answer(
        "برای ثبت، دکمه سبز «تأیید و ثبت» را بزنید. برای انصراف دکمه قرمز.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [ibtn("تأیید و ثبت", callback_data="ann:ok", style=SUCCESS)],
                [ibtn("انصراف", callback_data="wiz:cancel", style=DANGER)],
            ]
        ),
    )


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
        "اطلاع‌رسانی ثبت شد و در بخش اطلاع‌رسانی دیده می‌شود.\n"
        "اگر خودتان کانال دارید و می‌خواهید ربات سر ساعت رمز را بفرستد، از «ثبت کاستوم جایزه‌دار» استفاده کنید.",
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
