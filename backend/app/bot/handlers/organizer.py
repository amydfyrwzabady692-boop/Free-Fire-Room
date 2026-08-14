from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.access import menu_for
from app.bot.keyboards.common import wizard_nav
from app.bot.states.groups import EventWizardSG
from app.core.enums import OrganizerStatus
from app.core.time import parse_naive_in_tz
from app.models.channel import Channel, ChannelOwnership
from app.models.organizer import Organizer
from app.models.user import User
from app.services.channels import connect_organizer_channel
from app.services.events import create_event, submit_for_publish
from app.services.organizers import get_or_apply

router = Router(name="organizer")


@router.message(F.text == "ثبت کاستوم")
async def start_org(message: Message, db: AsyncSession, db_user: User, state: FSMContext):
    org = await get_or_apply(db, db_user, db_user.first_name)
    if org.status == OrganizerStatus.PENDING:
        await message.answer("درخواست برگزارکننده شما ثبت شد و منتظر تأیید مدیریت است.")
        return
    if org.status in {OrganizerStatus.REJECTED, OrganizerStatus.SUSPENDED}:
        await message.answer("حساب برگزارکننده شما فعال نیست. با پشتیبانی تماس بگیرید.")
        return
    await state.set_state(EventWizardSG.title)
    await message.answer("عنوان کاستوم را بفرستید.\nبرای انصراف /cancel", reply_markup=wizard_nav())


@router.message(F.text == "/cancel")
@router.callback_query(F.data == "wiz:cancel")
async def cancel_wiz(event: Message | CallbackQuery, state: FSMContext, db: AsyncSession, db_user: User):
    await state.clear()
    msg = event.message if isinstance(event, CallbackQuery) else event
    await msg.answer("ویزارد لغو شد.", reply_markup=await menu_for(db, db_user))
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.message(EventWizardSG.title)
async def wiz_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
    if len(title) < 3:
        await message.answer("عنوان خیلی کوتاه است.")
        return
    await state.update_data(title=title)
    await state.set_state(EventWizardSG.description)
    await message.answer("توضیحات را بفرستید یا «-» برای رد کردن.")


@router.message(EventWizardSG.description)
async def wiz_desc(message: Message, state: FSMContext):
    text = None if (message.text or "").strip() == "-" else message.text
    await state.update_data(description=text)
    await state.set_state(EventWizardSG.banner)
    await message.answer("بنر را به‌صورت عکس بفرستید یا «-» برای رد.")


@router.message(EventWizardSG.banner)
async def wiz_banner(message: Message, state: FSMContext):
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    await state.update_data(banner_file_id=file_id)
    await state.set_state(EventWizardSG.starts_at)
    await message.answer("زمان برگزاری را به صورت 1405-05-24 21:30 یا 2026-08-15 21:30 بفرستید (منطقه Asia/Tehran).")


def _parse_dt(text: str) -> datetime:
    text = text.strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d-%H:%M"):
        try:
            naive = datetime.strptime(text, fmt)
            # Jalali years ~1400+
            if naive.year < 1700:
                import jdatetime

                j = jdatetime.datetime(naive.year, naive.month, naive.day, naive.hour, naive.minute)
                g = j.togregorian()
                return parse_naive_in_tz(datetime(g.year, g.month, g.day, g.hour, g.minute), "Asia/Tehran")
            return parse_naive_in_tz(naive, "Asia/Tehran")
        except ValueError:
            continue
    raise ValueError("bad date")


@router.message(EventWizardSG.starts_at)
async def wiz_starts(message: Message, state: FSMContext):
    try:
        dt = _parse_dt(message.text or "")
    except ValueError:
        await message.answer("زمان نامعتبر است. نمونه: 2026-08-20 22:00")
        return
    await state.update_data(starts_at=dt.isoformat())
    await state.set_state(EventWizardSG.registration_ends_at)
    await message.answer("زمان پایان ثبت‌نام را بفرستید.")


@router.message(EventWizardSG.registration_ends_at)
async def wiz_reg_end(message: Message, state: FSMContext):
    try:
        dt = _parse_dt(message.text or "")
    except ValueError:
        await message.answer("زمان نامعتبر است.")
        return
    await state.update_data(registration_ends_at=dt.isoformat())
    await state.set_state(EventWizardSG.credentials_send_at)
    await message.answer("زمان ارسال Room ID و Password را بفرستید (نزدیک شروع بازی بهتر است).")


@router.message(EventWizardSG.credentials_send_at)
async def wiz_creds_at(message: Message, state: FSMContext):
    try:
        dt = _parse_dt(message.text or "")
    except ValueError:
        await message.answer("زمان نامعتبر است.")
        return
    await state.update_data(credentials_send_at=dt.isoformat())
    await state.set_state(EventWizardSG.channel)
    await message.answer("آیدی عددی کانال یا @username کانال را بفرستید. ربات باید ادمین باشد و شما مدیر کانال.")


@router.message(EventWizardSG.channel)
async def wiz_channel(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    ref = (message.text or "").strip()
    try:
        ch = await connect_organizer_channel(db, message.bot, db_user, int(ref) if ref.lstrip("-").isdigit() else ref)
    except Exception as exc:  # noqa: BLE001
        await message.answer(str(getattr(exc, "message", exc)))
        return
    await state.update_data(channel_id=str(ch.id), required_channel_ids=[str(ch.id)])
    await state.set_state(EventWizardSG.region_mode)
    await message.answer("منطقه و حالت را بفرستید؛ مثال: ME squad")


@router.message(EventWizardSG.region_mode)
async def wiz_region(message: Message, state: FSMContext):
    parts = (message.text or "").split()
    region = parts[0].upper() if parts else "ME"
    mode = parts[1].lower() if len(parts) > 1 else "squad"
    if mode not in {"solo", "duo", "squad"}:
        mode = "squad"
    await state.update_data(region=region, game_mode=mode)
    await state.set_state(EventWizardSG.capacity)
    await message.answer("ظرفیت اتاق؟ عدد بین ۱ تا ۱۰۰")


@router.message(EventWizardSG.capacity)
async def wiz_cap(message: Message, state: FSMContext):
    try:
        cap = int(message.text or "")
        if not 1 <= cap <= 100:
            raise ValueError
    except ValueError:
        await message.answer("عدد معتبر بفرستید.")
        return
    await state.update_data(capacity=cap)
    await state.set_state(EventWizardSG.prizes)
    await message.answer("خلاصه جوایز را بفرستید.")


@router.message(EventWizardSG.prizes)
async def wiz_prizes(message: Message, state: FSMContext):
    await state.update_data(prize_summary=message.text, prizes=[{"place": 1, "title": message.text or "جایزه"}])
    await state.set_state(EventWizardSG.rules)
    await message.answer("قوانین کاستوم را بفرستید.")


@router.message(EventWizardSG.rules)
async def wiz_rules(message: Message, state: FSMContext):
    await state.update_data(rules_text=message.text, require_rules_accept=True)
    await state.set_state(EventWizardSG.requirements)
    await message.answer("تعداد دعوت معتبر لازم؟ عدد (۰ اگر لازم نیست). سقف را مدیر کل تعیین می‌کند.")


@router.message(EventWizardSG.requirements)
async def wiz_req(message: Message, state: FSMContext):
    try:
        n = int(message.text or "0")
    except ValueError:
        n = 0
    await state.update_data(required_referrals=max(0, n), waitlist_enabled=True, visibility="public")
    await state.set_state(EventWizardSG.room)
    await message.answer("Room ID و Password را در یک خط بفرستید؛ مثال:\n12345678 mypass")


@router.message(EventWizardSG.room)
async def wiz_room(message: Message, state: FSMContext):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("هر دو مقدار لازم است.")
        return
    await state.update_data(room_id=parts[0], room_password=parts[1])
    data = await state.get_data()
    await state.set_state(EventWizardSG.preview)
    await message.answer(
        "پیش‌نمایش:\n"
        f"عنوان: {data.get('title')}\n"
        f"شروع: {data.get('starts_at')}\n"
        f"پایان ثبت‌نام: {data.get('registration_ends_at')}\n"
        f"ارسال رمز: {data.get('credentials_send_at')}\n"
        f"ظرفیت: {data.get('capacity')} | {data.get('game_mode')} | {data.get('region')}\n"
        f"دعوت لازم: {data.get('required_referrals')}\n"
        f"جایزه: {data.get('prize_summary')}\n\n"
        "برای ذخیره پیش‌نویس: پیش‌نویس\nبرای ارسال جهت تأیید: انتشار"
    )


@router.message(EventWizardSG.preview, F.text.in_({"پیش‌نویس", "انتشار"}))
async def wiz_finish(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    from datetime import datetime as dt
    from uuid import UUID

    data = await state.get_data()
    org = await db.scalar(select(Organizer).where(Organizer.user_id == db_user.id))
    payload = {
        **data,
        "starts_at": dt.fromisoformat(data["starts_at"]),
        "registration_ends_at": dt.fromisoformat(data["registration_ends_at"]),
        "credentials_send_at": dt.fromisoformat(data["credentials_send_at"]),
        "channel_id": UUID(data["channel_id"]),
        "required_channel_ids": [UUID(x) for x in data.get("required_channel_ids") or []],
    }
    try:
        event = await create_event(db, org, payload, db_user.id)
        if message.text == "انتشار":
            await submit_for_publish(db, event, db_user.id)
            await message.answer(
                f"کاستوم ثبت شد. وضعیت: {event.status}\nلینک اختصاصی پس از انتشار در پنل و ربات نمایش داده می‌شود.",
                reply_markup=await menu_for(db, db_user),
            )
        else:
            await message.answer(f"پیش‌نویس ذخیره شد. شناسه: {event.id}", reply_markup=await menu_for(db, db_user))
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"خطا: {getattr(exc, 'message', exc)}")
        return
    await state.clear()
