from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.access import menu_for
from app.bot.helpers import ack_callback, reply_callback
from app.bot.keyboards.common import (
    home_kb,
    labeled,
    organizer_reply_kb,
    winner_claim_review_kb,
    winner_list_kb,
)
from app.bot.onboarding import ensure_onboarding
from app.bot.states.groups import WinnerChatSG, WinnerSG
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.rate_limit import hit_rate_limit
from app.models.admin import Admin
from app.models.event import Event
from app.models.user import User
from app.core.enums import WinnerMessageDirection
from app.services.winners import (
    check_winner_eligibility,
    claim_parties,
    create_winner_claim,
    format_relayed_to_organizer,
    format_winner_claim_caption,
    list_recent_winner_events,
    organizer_telegram_id,
    player_dm_link,
    record_message,
)

router = Router(name="winner")
log = get_logger(__name__)


def _list_label(e: Event) -> str:
    prize = (e.prize_summary or "").strip().replace("\n", " ")
    label = prize[:28] if prize else e.title
    return f"🎁 {label}"


async def _event_by_token(db: AsyncSession, token: str | None) -> Event | None:
    if not token:
        return None
    return await db.scalar(select(Event).where(Event.public_token == token, Event.deleted_at.is_(None)))


async def _notify_winner_claim(
    bot, db: AsyncSession, event: Event, player: User, file_id: str, claim
) -> None:
    """The organizer gets approve / reject / message right under the screenshot."""
    caption = format_winner_claim_caption(event, player)[:1024]
    kb = winner_claim_review_kb(str(claim.id), player_url=player_dm_link(player))
    targets: set[int] = set()
    org_tid = await organizer_telegram_id(db, event.organizer_id)
    if org_tid:
        targets.add(org_tid)
    admins = (await db.scalars(select(Admin).where(Admin.is_active.is_(True)))).all()
    for admin in admins:
        user = await db.get(User, admin.user_id)
        if user and user.telegram_id:
            targets.add(user.telegram_id)
    for chat_id in targets:
        try:
            await bot.send_photo(chat_id, file_id, caption=caption, reply_markup=kb)
        except Exception:
            try:
                await bot.send_message(chat_id, caption, reply_markup=kb)
            except Exception:
                log.exception("winner_claim_notify_failed", chat_id=chat_id)


@router.message(Command("winner"))
@router.message(F.text.in_(labeled("برنده", "برنده شدم")))
async def winner_entry(message: Message, db: AsyncSession, db_user: User, state: FSMContext):
    await state.clear()
    if not await ensure_onboarding(message, db_user, db, recheck_channels=False):
        return
    hours = get_settings().past_events_hours
    rows = await list_recent_winner_events(db, hours=hours)
    if not rows:
        await message.answer(
            "الان کاستومی برای اعلام برنده نیست.\n"
            "بعد از شروع کاستوم، از همین بخش کاستوم را انتخاب کنید و اسکرین برنده را بفرستید.",
            reply_markup=home_kb(),
        )
        return
    await message.answer(
        "🏆 <b>برنده</b>\n"
        "کاستومی که در آن برنده شده‌اید را انتخاب کنید، بعد اسکرین برنده شدن را بفرستید.\n\n"
        "اگر شرایط جوین را انجام نداده باشید و ROOM ID / PASS را از ربات نگرفته باشید، جایزه تعلق نمی‌گیرد.",
        reply_markup=winner_list_kb([(e.public_token, _list_label(e)) for e in rows]),
    )


@router.callback_query(F.data.startswith("win:"))
async def winner_pick(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    await ack_callback(cb)
    token = cb.data.split(":", 1)[1]
    e = await _event_by_token(db, token)
    if not e:
        await reply_callback(cb, "این کاستوم یافت نشد.")
        return
    reason = await check_winner_eligibility(db, db_user, e)
    if reason:
        await reply_callback(cb, reason)
        return
    await state.set_state(WinnerSG.screenshot)
    await state.update_data(event_token=token)
    await reply_callback(cb, "اسکرین‌شات برنده شدنتان را همین‌جا بفرستید.\nفقط عکس.")


@router.message(WinnerSG.screenshot)
async def winner_screenshot(message: Message, db: AsyncSession, db_user: User, state: FSMContext):
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        file_id = message.document.file_id
    if not file_id:
        await message.answer("یک عکس اسکرین بفرستید، یا «لغو» را بزنید.")
        return
    try:
        await hit_rate_limit(f"rl:win:{db_user.telegram_id}", 4)
    except AppError as exc:
        await message.answer(exc.message)
        return
    data = await state.get_data()
    e = await _event_by_token(db, data.get("event_token"))
    if not e:
        await state.clear()
        await message.answer("کاستوم یافت نشد.", reply_markup=await menu_for(db, db_user))
        return
    try:
        claim = await create_winner_claim(db, user=db_user, event=e, screenshot_file_id=file_id)
        await db.commit()
    except AppError as exc:
        await state.clear()
        await message.answer(exc.message, reply_markup=await menu_for(db, db_user))
        return
    except Exception:
        log.exception("winner_claim_failed")
        await db.rollback()
        await message.answer("ثبت اسکرین الان انجام نشد. چند ثانیه بعد دوباره تلاش کنید.")
        return
    await _notify_winner_claim(message.bot, db, e, db_user, file_id, claim)
    await state.clear()
    await message.answer(
        "🏆 اسکرین برنده ثبت شد و برای برگزارکننده و مالک ربات ارسال شد.\n"
        "بعد از تأیید، آیدی دریافت جایزه همین‌جا برایتان می‌آید و می‌توانید مستقیم با برگزارکننده پیام بدهید.",
        reply_markup=await menu_for(db, db_user),
    )


# ---------------------------------------------------------------- winner replies


@router.callback_query(F.data.startswith("winr:"))
async def winner_reply_start(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    """The winner answers the organizer without leaving the bot."""
    await ack_callback(cb)
    claim = await _own_claim(db, db_user, cb.data.split(":", 1)[1])
    if not claim:
        await reply_callback(cb, "این گفت‌وگو در دسترس نیست.")
        return
    await state.set_state(WinnerChatSG.to_organizer)
    await state.update_data(claim_id=str(claim.id))
    await reply_callback(
        cb,
        "✉️ پیامتان برای برگزارکننده فرستاده می‌شود. متن را همین‌جا بنویسید.\n"
        "برای انصراف «لغو» را بزنید.",
    )


async def _own_claim(db: AsyncSession, user: User, raw: str):
    from uuid import UUID

    from app.models.winner import WinnerClaim

    try:
        claim_id = UUID(raw)
    except ValueError:
        return None
    claim = await db.scalar(
        select(WinnerClaim)
        .where(WinnerClaim.id == claim_id, WinnerClaim.user_id == user.id)
        .options(selectinload(WinnerClaim.event))
    )
    return claim


@router.message(WinnerChatSG.to_organizer)
async def winner_reply_body(message: Message, db: AsyncSession, db_user: User, state: FSMContext):
    body = (message.text or "").strip()
    if not body:
        await message.answer("متن پیام را بنویسید، یا «لغو» را بزنید.")
        return
    if len(body) > 1000:
        await message.answer("پیام حداکثر ۱۰۰۰ حرف باشد.")
        return
    try:
        await hit_rate_limit(f"rl:winmsg:{db_user.telegram_id}", 6)
    except AppError as exc:
        await message.answer(exc.message)
        return
    data = await state.get_data()
    claim = await _own_claim(db, db_user, data.get("claim_id") or "")
    if not claim or not claim.event:
        await state.clear()
        await message.answer("این گفت‌وگو دیگر در دسترس نیست.", reply_markup=await menu_for(db, db_user))
        return
    _, organizer_user = await claim_parties(db, claim)
    delivered = False
    if organizer_user and not organizer_user.is_bot_blocked:
        player_url = player_dm_link(db_user)
        try:
            await message.bot.send_message(
                organizer_user.telegram_id,
                format_relayed_to_organizer(claim.event, db_user, body),
                reply_markup=organizer_reply_kb(str(claim.id), player_url=player_url),
            )
            delivered = True
        except Exception:  # noqa: BLE001
            delivered = False
    await record_message(
        db,
        claim=claim,
        sender_id=db_user.id,
        body=body,
        delivered=delivered,
        direction=WinnerMessageDirection.TO_ORGANIZER,
    )
    await db.commit()
    await state.clear()
    await message.answer(
        "✉️ پیام شما برای برگزارکننده ارسال شد."
        if delivered
        else "پیام ثبت شد ولی به برگزارکننده نرسید. از «گزارش به مالک ربات» هم می‌توانید استفاده کنید.",
        reply_markup=await menu_for(db, db_user),
    )
