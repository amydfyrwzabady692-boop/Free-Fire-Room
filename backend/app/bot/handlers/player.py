from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.bot.access import menu_for
from app.bot.helpers import ack_callback, esc, replace_callback_view, reply_callback
from app.bot.keyboards.common import (
    DANGER,
    PRIMARY,
    SUCCESS,
    checklist_kb,
    event_detail_kb,
    event_list_kb,
    help_back_kb,
    help_kb,
    home_kb,
    ibtn,
    labeled,
    report_reasons_kb,
    review_comment_kb,
    review_prize_kb,
    review_stars_kb,
    share_link_kb,
)
from app.bot.onboarding import ensure_onboarding
from app.bot.states.groups import ReportSG, ReviewSG, SupportSG
from app.core.config import get_settings
from app.core.enums import EventStatus, EventVisibility, RegistrationStatus, ReportReason, RequirementType
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.rate_limit import hit_rate_limit
from app.core.time import format_local
from app.locales import fa as T
from app.locales.labels import event_status_fa, reg_status_fa
from app.models.event import Event, RoomCredential
from app.models.organizer import Organizer
from app.models.registration import Registration
from app.models.user import User, UserProfile
from app.services.referrals import apply_start_referral, get_or_create_link
from app.services.registration import register_user
from app.services.reports import (
    create_player_report,
    creds_were_provided,
    event_missed_credentials,
    format_cheater_alert_for_organizer,
    format_person,
    format_prize_vote_alert,
    format_report_alert,
    join_window_open,
    notify_active_admins,
    notify_telegram_user,
)
from app.services.requirements import evaluate_requirements
from app.services.reviews import (
    can_review,
    create_review,
    format_rating_line,
    format_review_item,
    list_event_reviews,
    review_summary_for_event,
    review_summary_for_organizer,
)

router = Router(name="player")
log = get_logger(__name__)


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


async def _ensure_onboarding(
    message: Message, user: User, db: AsyncSession, *, recheck_channels: bool = True
) -> bool:
    return await ensure_onboarding(message, user, db, recheck_channels=recheck_channels)


async def _show_panel(event: Message | CallbackQuery, text: str, markup) -> None:
    if isinstance(event, CallbackQuery):
        await replace_callback_view(event, text, inline=markup)
        return
    await event.answer(text, reply_markup=markup)


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, db: AsyncSession, db_user: User, state: FSMContext):
    await state.clear()
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
    await _welcome_after_onboarding(message, db, db_user)


async def _welcome_after_onboarding(message: Message, db: AsyncSession, db_user: User) -> None:
    kind, token = _parse_start(db_user.start_payload)
    if kind == "event" and token:
        await message.answer(
            "از لینک اختصاصی این کاستوم آمدید.\n"
            "🎁 جایزه، شرایط و کانال‌های جوین اجباری را ببینید؛ عضو شوید تا سر ساعت ROOM ID و PASS برایتان بیاید.",
            reply_markup=await menu_for(db, db_user),
        )
        await _show_event(message, db, db_user, token)
        return
    await message.answer(
        f"{T.INTRO}\n\n✨ منوی اصلی آماده است. جزئیات بیشتر در «راهنما و قوانین».",
        reply_markup=await menu_for(db, db_user),
    )


@router.message(F.text.in_(labeled("شروع مجدد")))
async def restart_menu(message: Message, db: AsyncSession, db_user: User, state: FSMContext):
    await state.clear()
    db_user.start_payload = None
    await db.flush()
    if not await _ensure_onboarding(message, db_user, db):
        return
    await _welcome_after_onboarding(message, db, db_user)


@router.message(Command("cancel"))
@router.message(F.text.in_(labeled("لغو", "انصراف") | {"/cancel"}))
async def cancel_flow(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    await state.clear()
    await message.answer("لغو شد.", reply_markup=await menu_for(db, db_user))


@router.callback_query(F.data == "tos:accept")
async def tos_accept(cb: CallbackQuery, db: AsyncSession, db_user: User):
    db_user.tos_accepted_at = datetime.now(UTC)
    db_user.privacy_accepted_at = datetime.now(UTC)
    await db.flush()
    await cb.message.answer("شرایط پذیرفته شد.")
    if await _ensure_onboarding(cb.message, db_user, db):
        await _welcome_after_onboarding(cb.message, db, db_user)
    await cb.answer()


@router.callback_query(F.data == "tos:privacy")
async def tos_privacy(cb: CallbackQuery):
    await cb.message.answer(T.PRIVACY)
    await cb.answer()


@router.callback_query(F.data == "membership:recheck")
async def membership_recheck(cb: CallbackQuery, db: AsyncSession, db_user: User):
    await hit_rate_limit(f"rl:mem:{db_user.telegram_id}", get_settings().rate_limit_membership_per_minute)
    if await _ensure_onboarding(cb.message, db_user, db):
        await _welcome_after_onboarding(cb.message, db, db_user)
    await cb.answer()


async def _list_events(db: AsyncSession, *, mode: str = "upcoming") -> list[Event]:
    now = datetime.now(UTC)
    hours = get_settings().past_events_hours
    fill = get_settings().custom_fill_minutes + get_settings().credentials_grace_minutes
    live = [EventStatus.PUBLISHED, EventStatus.FULL, EventStatus.STARTED]
    listed = live + [EventStatus.FINISHED]
    stmt = (
        select(Event)
        .where(
            Event.deleted_at.is_(None),
            Event.visibility == EventVisibility.PUBLIC,
            Event.deep_link_active.is_(True),
        )
        .options(selectinload(Event.organizer), selectinload(Event.channel))
        .limit(20)
    )
    if mode == "past":
        stmt = stmt.where(
            Event.status.in_(listed),
            Event.starts_at < now,
            Event.starts_at >= now - timedelta(hours=hours),
        ).order_by(Event.starts_at.desc())
    elif mode == "today":
        from app.core.time import local_day_bounds

        start, end = local_day_bounds()
        stmt = stmt.where(Event.status.in_(listed), Event.starts_at >= start, Event.starts_at < end).order_by(
            Event.starts_at.asc()
        )
    else:
        stmt = stmt.where(
            Event.status.in_(live),
            Event.starts_at >= now - timedelta(minutes=fill),
        ).order_by(Event.starts_at.asc())
    return list((await db.scalars(stmt)).all())


def _join_urls(items) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for item in items or []:
        url = getattr(item, "url", None)
        if not url:
            continue
        label = (item.label or "کانال").replace("عضویت در ", "")
        out.append((label, url))
    return out


def _list_title(e: Event) -> str:
    stamp = format_local(e.starts_at, e.timezone, compact=True)
    prize = (e.prize_summary or "").strip().replace("\n", " ")
    label = prize[:28] if prize else e.title
    if e.starts_at < datetime.now(UTC):
        return f"گذشته · 🕐 {stamp} · 🎁 {label}"
    return f"🕐 {stamp} · 🎁 {label}"


async def _event_card(db: AsyncSession, e: Event, *, missed: bool = False) -> str:
    org = e.organizer.display_name if e.organizer else "برگزارکننده"
    ch = e.channel.title if e.channel else "-"
    now = datetime.now(UTC)
    left = max(0, int((e.starts_at - now).total_seconds() // 60))
    grace = get_settings().credentials_grace_minutes
    fill = get_settings().custom_fill_minutes
    extra = (
        f"🆔 سر همین ساعت برگزارکننده حداکثر {grace} دقیقه فرصت دارد ROOM ID و PASS را داخل ربات بفرستد.\n"
        f"بعد از ساعت شروع، {fill} دقیقه هم برای پر شدن کاستوم فرصت هست؛ "
        "اگر در این مدت شرایط را کامل کنید مشخصات برایتان می‌آید."
    )
    if missed:
        extra = (
            f"⚠️ برگزارکننده در مهلت {grace} دقیقه‌ای ROOM ID و PASS را نفرستاد.\n"
            "اگر ثبت‌نام کرده بودید، گزارش بدهید و نظر/امتیاز ثبت کنید."
        )
    elif e.status == EventStatus.CANCELLED:
        extra = (
            "این کاستوم لغو شد. ROOM ID و PASS ارسال نمی‌شود. "
            "اگر ثبت‌نام کرده بودید می‌توانید گزارش بدهید یا نظر بگذارید."
        )
    elif e.starts_at <= now:
        extra = (
            f"🕐 ساعت کاستوم رسیده. برگزارکننده {grace} دقیقه برای ارسال ROOM ID / PASS فرصت دارد "
            f"و {fill} دقیقه برای پر شدن کاستوم. اگر الان جوین را کامل کنید مشخصات برایتان می‌آید."
        )
    org_line = format_rating_line(await review_summary_for_organizer(db, e.organizer_id), prefix="سابقه برگزارکننده")
    ev_line = format_rating_line(await review_summary_for_event(db, e.id), prefix="امتیاز این کاستوم")
    prize = (e.prize_summary or "").strip()
    if not prize and e.prizes:
        prize = "\n".join(f"{p.place}. {p.title}" for p in e.prizes if p.title)
    heading = (e.title or "").strip()
    prize = prize or "اعلام نشده"
    heading_html = f"<b>{esc(heading)}</b>\n\n" if heading and heading != prize else ""
    left_line = f"مانده: {left} دقیقه" if left else "ساعت کاستوم رسیده"
    return (
        "🎮 <b>کاستوم جایزه‌دار</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"{heading_html}"
        f"💎 <b>جایزه</b>\n{esc(prize)}\n"
        "━━━━━━━━━━━━━━\n"
        f"🕐 {format_local(e.starts_at, e.timezone)}\n"
        f"⏳ {left_line}\n"
        f"👤 برگزارکننده: {esc(org)}\n"
        f"{org_line}\n"
        f"{ev_line}\n"
        f"📢 کانال برگزارکننده: {esc(ch)}\n\n"
        f"{extra}"
    )


@router.message(Command("customs"))
@router.message(F.text.in_(labeled("کاستوم‌های آینده", "کاستوم‌های جایزه‌دار")))
@router.callback_query(F.data == "list:upcoming")
async def upcoming(event: Message | CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    await state.clear()
    msg = event.message if isinstance(event, CallbackQuery) else event
    if not await _ensure_onboarding(msg, db_user, db, recheck_channels=not isinstance(event, CallbackQuery)):
        if isinstance(event, CallbackQuery):
            await ack_callback(event)
        return
    rows = await _list_events(db, mode="upcoming")
    await state.update_data(list_mode="upcoming")
    hours = get_settings().past_events_hours
    if not rows:
        text = (
            "الان کاستوم پیش‌رویی نیست. می‌توانید خودتان با «ثبت کاستوم» بگذارید، "
            f"یا کاستوم‌های {hours} ساعت گذشته را ببینید."
        )
        await _show_panel(event, text, event_list_kb([], mode="upcoming"))
        return
    kb = event_list_kb([(e.public_token, _list_title(e)) for e in rows], mode="upcoming")
    text = (
        "🔥 <b>کاستوم‌های جایزه‌دار پیش‌رو</b>\n"
        "روی مورد بزنید تا جایزه، ساعت و کانال‌های جوین اجباری را ببینید. اگر عکس باشد جدا می‌آید.\n"
        "بعد عضو شوید و دکمه سبز «عضو شدم» را بزنید تا سر ساعت ROOM ID و PASS برایتان بیاید.\n"
        f"کاستوم‌های {hours} ساعت گذشته هم از دکمه پایین در دسترس است."
    )
    await _show_panel(event, text, kb)


@router.callback_query(F.data == "list:past")
async def past_customs(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    await state.clear()
    if not await _ensure_onboarding(cb.message, db_user, db, recheck_channels=False):
        await ack_callback(cb)
        return
    hours = get_settings().past_events_hours
    rows = await _list_events(db, mode="past")
    await state.update_data(list_mode="past")
    if not rows:
        await replace_callback_view(
            cb,
            f"در {hours} ساعت گذشته کاستومی نبود.",
            inline=event_list_kb([], mode="past"),
        )
        return
    await replace_callback_view(
        cb,
        f"کاستوم‌های {hours} ساعت گذشته:\n"
        "ببینید چه کسی گذاشته و نظرات/امتیاز بازیکن‌ها چیست.",
        inline=event_list_kb([(e.public_token, _list_title(e)) for e in rows], mode="past"),
    )


@router.message(F.text.in_(labeled("کاستوم‌های امروز")))
@router.callback_query(F.data == "list:today")
async def today(event: Message | CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    await state.clear()
    msg = event.message if isinstance(event, CallbackQuery) else event
    if not await _ensure_onboarding(msg, db_user, db, recheck_channels=not isinstance(event, CallbackQuery)):
        if isinstance(event, CallbackQuery):
            await ack_callback(event)
        return
    rows = await _list_events(db, mode="today")
    await state.update_data(list_mode="today")
    if not rows:
        await _show_panel(
            event,
            "برای امروز کاستومی نیست. از دکمه پایین همه پیش‌روها را ببینید.",
            event_list_kb([], mode="today"),
        )
        return
    await _show_panel(
        event,
        "🔥 <b>کاستوم‌های امروز</b>\nروی مورد بزنید تا جایزه و کانال‌های جوین را ببینید. اگر عکس باشد جدا می‌آید.",
        event_list_kb([(e.public_token, _list_title(e)) for e in rows], mode="today"),
    )


@router.callback_query(F.data.startswith("ev:"))
async def show_event_cb(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    current = await state.get_state()
    if current and (current.startswith("ReviewSG") or current.startswith("ReportSG")):
        await state.clear()
    token = cb.data.split(":", 1)[1]
    if not await _ensure_onboarding(cb.message, db_user, db, recheck_channels=False):
        await ack_callback(cb)
        return
    back = (await state.get_data()).get("list_mode") or "upcoming"
    await _show_event(cb.message, db, db_user, token, back=back)
    await ack_callback(cb)


async def _event_by_token(db: AsyncSession, token: str | None) -> Event | None:
    if not token:
        return None
    return await db.scalar(
        select(Event)
        .where(Event.public_token == token, Event.deleted_at.is_(None))
        .options(
            selectinload(Event.organizer),
            selectinload(Event.channel),
            selectinload(Event.prizes),
            selectinload(Event.required_channels),
        )
    )


async def _show_event(message: Message, db: AsyncSession, user: User, token: str, *, back: str = "upcoming"):
    e = await _event_by_token(db, token)
    if not e or e.deleted_at:
        await message.answer("این کاستوم در دسترس نیست یا حذف شده است.")
        return
    cancelled = e.status == EventStatus.CANCELLED
    if not e.deep_link_active and not cancelled:
        await message.answer("این کاستوم در دسترس نیست یا لغو شده است.")
        return
    reg = await db.scalar(select(Registration).where(Registration.event_id == e.id, Registration.user_id == user.id))
    checklist = await evaluate_requirements(db, user=user, event=e, bot=message.bot, registration=reg)
    channel_items = [
        item
        for item in checklist.items
        if item.requirement_type
        in {RequirementType.CHANNEL_MEMBERSHIP, RequirementType.GLOBAL_CHANNEL_MEMBERSHIP}
    ]
    missed = await event_missed_credentials(db, e)
    started = datetime.now(UTC) >= e.starts_at
    filling = join_window_open(e)
    allowed, _ = await can_review(db, user, e)
    summary = await review_summary_for_event(db, e.id)
    text = await _event_card(db, e, missed=missed)
    text += "\n━━━━━━━━━━━━━━\n✅ <b>شرایط شرکت</b>\n"
    text += "باید در کانال‌های زیر عضو بمانید تا سر ساعت ROOM ID و PASS برایتان بیاید:\n"
    if channel_items:
        for item in channel_items:
            mark = "✅" if item.status == "done" else "❌"
            text += f"{mark} {esc(item.label)}\n"
    else:
        text += "کانال جوین اجباری ثبت نشده است.\n"
    if missed:
        text += "\nROOM ID / PASS ارسال نشد. گزارش بدهید و اگر ثبت‌نام کرده بودید نظر/امتیاز بگذارید."
    elif cancelled:
        text += "\nاین کاستوم لغو شده است."
    elif filling:
        text += "\nبعد از جوین، دکمه سبز «عضو شدم» را بزنید. تا پر شدن کاستوم اگر شرایط را کامل کنید مشخصات برایتان می‌آید."
    elif not started:
        text += "\nبعد از جوین، دکمه سبز «عضو شدم» را بزنید."
    else:
        text += "\nاگر ROOM ID / PASS نیامد یا جایزه نداد: «گزارش به مالک ربات»."
    open_for_join = e.status in {EventStatus.PUBLISHED, EventStatus.FULL, EventStatus.STARTED} and filling
    kb = event_detail_kb(
        token,
        join_urls=_join_urls(channel_items),
        can_join=open_for_join and not missed,
        can_review=allowed,
        show_reviews=summary["count"] > 0 or started or cancelled,
        can_claim_win=started and not cancelled,
        back=back,
    )
    photo = e.banner_file_id
    if photo:
        try:
            await message.answer_photo(photo)
        except Exception:
            pass
    await message.answer(text, reply_markup=kb)


async def _queue_late_credentials(db: AsyncSession, event: Event) -> None:
    if not join_window_open(event):
        return
    creds = await db.scalar(select(RoomCredential).where(RoomCredential.event_id == event.id))
    if not creds_were_provided(creds):
        return
    await db.commit()
    from app.workers.tasks import send_event_credentials

    send_event_credentials.delay(str(event.id))


async def _send_join_result(cb: CallbackQuery, event: Event, token: str, result, db: AsyncSession) -> None:
    if result.registration.status == RegistrationStatus.CONFIRMED:
        await _queue_late_credentials(db, event)
        if datetime.now(UTC) >= event.starts_at:
            await reply_callback(
                cb,
                "✅ ثبت‌نام شد. اگر ROOM ID ارسال شده باشد و هنوز در کانال‌ها باشید، الان برایتان می‌آید.",
            )
            return
        await reply_callback(
            cb,
            f"✅ ثبت‌نام شد. سر ساعت {format_local(event.credentials_send_at, event.timezone)} "
            "برگزارکننده ROOM ID و PASS را در ربات می‌فرستد و اگر هنوز عضو کانال‌ها باشید برایتان می‌آید.",
        )
        return
    if result.waitlisted:
        await reply_callback(cb, "ظرفیت پر است. شما در لیست انتظار قرار گرفتید.")
        return
    text = "هنوز در این کانال‌ها عضو نیستید:\n"
    for item in result.checklist or []:
        if item.requirement_type not in {
            RequirementType.CHANNEL_MEMBERSHIP,
            RequirementType.GLOBAL_CHANNEL_MEMBERSHIP,
        }:
            continue
        mark = "✅" if item.status == "done" else "❌"
        text += f"{mark} {esc(item.label)}\n"
    await reply_callback(cb, text, reply_markup=checklist_kb(token, join_urls=_join_urls(result.checklist)))


@router.callback_query(F.data.startswith("join:"))
async def join_event(cb: CallbackQuery, db: AsyncSession, db_user: User):
    await ack_callback(cb)
    try:
        await hit_rate_limit(f"rl:reg:{db_user.telegram_id}", get_settings().rate_limit_register_per_minute)
        msg = cb.message
        if msg is not None and hasattr(msg, "answer"):
            if not await _ensure_onboarding(msg, db_user, db):
                return
        elif not db_user.tos_accepted_at:
            await reply_callback(cb, T.TOS)
            return
        token = cb.data.split(":", 1)[1]
        e = await _event_by_token(db, token)
        if not e:
            await reply_callback(cb, "این کاستوم یافت نشد.")
            return
        source = "deep_link" if (db_user.start_payload or "") == f"event_{token}" else "bot"
        result = await register_user(db, user=db_user, event=e, bot=cb.bot, source=source, accept_rules=True)
    except AppError as exc:
        if exc.code == "already_registered":
            if "e" in locals() and e is not None:
                await _queue_late_credentials(db, e)
            await reply_callback(
                cb,
                "قبلاً ثبت‌نام شده‌اید. اگر ROOM ID ارسال شده و هنوز عضو کانال‌ها باشید، الان برایتان می‌آید.",
            )
        else:
            await reply_callback(cb, exc.message)
        return
    except Exception:
        log.exception("join_event_failed")
        await db.rollback()
        await reply_callback(cb, "ثبت‌نام الان انجام نشد. چند ثانیه بعد دوباره «عضو شدم» را بزنید.")
        return
    await _send_join_result(cb, e, token, result, db)


@router.callback_query(F.data.startswith("req:"))
async def recheck_req(cb: CallbackQuery, db: AsyncSession, db_user: User):
    await ack_callback(cb)
    try:
        await hit_rate_limit(f"rl:mem:{db_user.telegram_id}", get_settings().rate_limit_membership_per_minute)
        token = cb.data.split(":", 1)[1]
        e = await _event_by_token(db, token)
        if not e:
            await reply_callback(cb, "این کاستوم یافت نشد.")
            return
        reg = await db.scalar(
            select(Registration).where(Registration.event_id == e.id, Registration.user_id == db_user.id)
        )
        checklist = await evaluate_requirements(db, user=db_user, event=e, bot=cb.bot, registration=reg)
        text = "وضعیت عضویت:\n"
        for item in checklist.items:
            if item.requirement_type not in {
                RequirementType.CHANNEL_MEMBERSHIP,
                RequirementType.GLOBAL_CHANNEL_MEMBERSHIP,
            }:
                continue
            mark = "✅" if item.status == "done" else "❌"
            text += f"{mark} {esc(item.label)}\n"
        await reply_callback(cb, text, reply_markup=checklist_kb(token, join_urls=_join_urls(checklist.items)))
        if not checklist.all_ok:
            return
        result = await register_user(db, user=db_user, event=e, bot=cb.bot, source="recheck", accept_rules=True)
    except AppError as exc:
        if exc.code == "already_registered":
            if "e" in locals() and e is not None:
                await _queue_late_credentials(db, e)
            return
        await reply_callback(cb, exc.message)
        return
    except Exception:
        log.exception("recheck_req_failed")
        await db.rollback()
        await reply_callback(cb, "بررسی عضویت الان انجام نشد. چند ثانیه بعد دوباره تلاش کنید.")
        return
    await _send_join_result(cb, e, token, result, db)


@router.callback_query(F.data.startswith("rules:"))
async def accept_rules(cb: CallbackQuery, db: AsyncSession, db_user: User):
    token = cb.data.split(":", 1)[1]
    e = await _event_by_token(db, token)
    if not e:
        await cb.answer("یافت نشد", show_alert=True)
        return
    try:
        result = await register_user(db, user=db_user, event=e, bot=cb.bot, source="rules", accept_rules=True)
    except AppError as exc:
        await cb.message.answer(exc.message)
        await cb.answer()
        return
    if result.registration.status == RegistrationStatus.CONFIRMED:
        await _queue_late_credentials(db, e)
        await cb.message.answer("قوانین پذیرفته شد و ثبت‌نام قطعی شد.")
    else:
        await cb.message.answer("قوانین پذیرفته شد. سایر شرایط را کامل کنید.")
    await cb.answer()


@router.callback_query(F.data.startswith("inv:"))
async def invite(cb: CallbackQuery, db: AsyncSession, db_user: User):
    await hit_rate_limit(f"rl:ref:{db_user.telegram_id}", get_settings().rate_limit_referral_per_minute)
    token = cb.data.split(":", 1)[1]
    e = await _event_by_token(db, token)
    link = await get_or_create_link(db, db_user.id, e.id if e else None)
    bot_user = get_settings().bot_username
    url = f"https://t.me/{bot_user}?start=ref_{link.token}"
    await cb.message.answer(
        f"🔗 <b>لینک دعوت اختصاصی</b>\n<code>{esc(url)}</code>\n\n"
        f"✅ دعوت‌های معتبر: {link.valid_count}\n"
        "دعوت وقتی معتبر است که فرد جدید ربات را استارت کند و عضویت اجباری را کامل کند.",
        parse_mode="HTML",
        reply_markup=share_link_kb(url, open_label="ورود با لینک دعوت", copy_label="کپی لینک دعوت"),
    )
    await cb.answer()


@router.message(F.text.in_(labeled("دعوت دوستان", "دعوت")))
@router.callback_query(F.data == "menu:invite")
async def invite_global(event: Message | CallbackQuery, db: AsyncSession, db_user: User):
    msg = event.message if isinstance(event, CallbackQuery) else event
    if not await _ensure_onboarding(msg, db_user, db):
        if isinstance(event, CallbackQuery):
            await event.answer()
        return
    link = await get_or_create_link(db, db_user.id, None, campaign="global")
    url = f"https://t.me/{get_settings().bot_username}?start=ref_{link.token}"
    await msg.answer(
        "🔗 لینک دعوت اختصاصی شما:\n"
        f"✅ دعوت‌های معتبر: {link.valid_count}\n"
        "وقتی دوستتان ربات را استارت کند و عضویت اجباری را کامل کند، دعوت به‌نام شما ثبت می‌شود.",
        reply_markup=share_link_kb(url, open_label="ورود با لینک دعوت", copy_label="کپی لینک دعوت"),
    )
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.message(F.text.in_(labeled("ثبت‌نام‌های من", "ثبت نام های من")))
async def my_regs(message: Message, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ensure_onboarding(message, db_user, db):
        return
    await state.update_data(list_mode="upcoming")
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
        await message.answer(
            "هنوز در کاستومی ثبت‌نام نکرده‌اید.\nاز «کاستوم‌های جایزه‌دار» یا لینک کاستوم وارد شوید تا سر ساعت ROOM ID و PASS بگیرید.",
            reply_markup=event_list_kb([], mode="mine"),
        )
        return
    items = []
    text = "🎯 <b>ثبت‌نام‌های شما</b>\nبرای گزارش یا جزئیات، کاستوم را باز کنید:\n"
    for r in rows:
        if not r.event:
            continue
        prize = (r.event.prize_summary or "").strip().replace("\n", " ")
        label = prize[:40] if prize else r.event.title
        text += f"• {esc(label)} — {reg_status_fa(r.status)}\n"
        items.append((r.event.public_token, _list_title(r.event)))
    await message.answer(text, reply_markup=event_list_kb(items, mode="mine") if items else home_kb())


@router.message(F.text.in_(labeled("نتایج و تاریخچه", "تاریخچه")))
@router.callback_query(F.data == "menu:history")
async def history(event: Message | CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    msg = event.message if isinstance(event, CallbackQuery) else event
    if not await _ensure_onboarding(msg, db_user, db, recheck_channels=not isinstance(event, CallbackQuery)):
        if isinstance(event, CallbackQuery):
            await ack_callback(event)
        return
    await state.update_data(list_mode="past")
    rows = (
        await db.scalars(
            select(Event)
            .join(Registration, Registration.event_id == Event.id)
            .where(Registration.user_id == db_user.id, Event.status.in_([EventStatus.FINISHED, EventStatus.CANCELLED]))
            .limit(20)
        )
    ).all()
    if not rows:
        await _show_panel(
            event,
            "هنوز کاستوم تمام‌شده‌ای ندارید. بعد از برگزاری، همین‌جا برای گزارش و نظر می‌ماند.",
            event_list_kb([], mode="past"),
        )
        return
    items = []
    lines = ["<b>نتایج و تاریخچه</b>"]
    for e in rows:
        prize = (e.prize_summary or "").strip().replace("\n", " ")
        label = prize[:40] if prize else e.title
        lines.append(f"• {esc(label)} ({event_status_fa(e.status)})")
        items.append((e.public_token, _list_title(e)))
    await _show_panel(event, "\n".join(lines), event_list_kb(items, mode="past"))


@router.message(Command("help"))
@router.message(F.text.in_(labeled("راهنما و قوانین", "راهنما")))
async def help_msg(message: Message):
    await message.answer(T.HELP + "\n\n" + T.DISCLAIMER, reply_markup=help_kb())


@router.callback_query(F.data.startswith("help:"))
async def help_section(cb: CallbackQuery):
    key = cb.data.split(":", 1)[1]
    texts = {
        "home": T.HELP,
        "about": T.HELP_ABOUT,
        "play": T.HELP_PLAY,
        "host": T.HELP_HOST,
        "rules": T.HELP_RULES,
        "panels": T.HELP_PANELS,
        "faq": T.HELP_FAQ,
    }
    body = texts.get(key)
    if not body:
        await cb.answer()
        return
    kb = help_kb() if key == "home" else help_back_kb()
    await replace_callback_view(cb, body + "\n\n" + T.DISCLAIMER, inline=kb)


@router.message(F.text.in_(labeled("پشتیبانی")))
async def support(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    if not await _ensure_onboarding(message, db_user, db):
        return
    await state.set_state(SupportSG.message)
    await message.answer(
        "✉️ پیام خود را بنویسید. مستقیم به مالک ربات می‌رسد.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[ibtn("انصراف", callback_data="menu:home", style=DANGER)]]
        ),
    )


@router.message(SupportSG.message)
async def support_body(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    body = (message.text or "").strip()
    if body in labeled("لغو", "انصراف") | {"/cancel"}:
        await state.clear()
        await message.answer("لغو شد.", reply_markup=await menu_for(db, db_user))
        return
    if len(body) < 3:
        await message.answer("پیام خیلی کوتاه است.")
        return
    await notify_active_admins(
        message.bot,
        db,
        f"پیام پشتیبانی\nاز: {format_person(db_user)}\n\n{esc(body[:2000])}",
    )
    await state.clear()
    await message.answer("پیام برای مالک ربات ارسال شد.", reply_markup=await menu_for(db, db_user))


@router.message(F.text.in_(labeled("پروفایل")))
async def profile(message: Message, db: AsyncSession, db_user: User):
    if not await _ensure_onboarding(message, db_user, db):
        return
    ff = db_user.profile.ff_player_id if db_user.profile else "—"
    regs = await db.scalar(select(func.count()).select_from(Registration).where(Registration.user_id == db_user.id)) or 0
    link = await get_or_create_link(db, db_user.id, None, campaign="global")
    await message.answer(
        f"👤 <b>پروفایل شما</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"شناسه تلگرام: <code>{db_user.telegram_id}</code>\n"
        f"نام: {esc(db_user.first_name)}\n"
        f"🎮 Free Fire ID: {esc(ff)}\n"
        f"🕐 منطقه زمانی: {esc(db_user.timezone)}\n"
        f"🎯 کاستوم‌های ثبت‌شده: {regs}\n"
        f"🔗 دعوت‌های معتبر: {link.valid_count}\n\n"
        "برای تنظیم شناسه بازیکن بنویسید:\n<code>/setid شناسه</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [ibtn("دعوت دوستان", callback_data="menu:invite", style=SUCCESS)],
                [ibtn("اعلان‌های من", callback_data="menu:notifs", style=PRIMARY)],
                [ibtn("بازگشت به منو", callback_data="menu:home", style=DANGER)],
            ]
        ),
    )


@router.message(F.text.startswith("/setid"))
async def set_id(message: Message, db_user: User, db: AsyncSession):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("مثال: /setid 123456789")
        return
    if db_user.profile:
        db_user.profile.ff_player_id = parts[1].strip()[:32]
    else:
        db.add(UserProfile(user_id=db_user.id, ff_player_id=parts[1].strip()[:32]))
    await message.answer("شناسه Free Fire ذخیره شد.")


@router.message(F.text.in_(labeled("اعلان‌های من", "اعلان ها")))
@router.callback_query(F.data == "menu:notifs")
async def notifs(event: Message | CallbackQuery, db: AsyncSession, db_user: User):
    from app.models.jobs import Notification

    msg = event.message if isinstance(event, CallbackQuery) else event
    if not await _ensure_onboarding(msg, db_user, db, recheck_channels=not isinstance(event, CallbackQuery)):
        if isinstance(event, CallbackQuery):
            await ack_callback(event)
        return
    enabled = bool(db_user.notification_enabled)
    toggle = "خاموش کردن اعلان روزانه" if enabled else "روشن کردن اعلان روزانه"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [ibtn(toggle, callback_data="notifs:toggle", style=DANGER if enabled else SUCCESS)],
            [ibtn("بازگشت به منو", callback_data="menu:home", style=PRIMARY)],
        ]
    )
    status = "روشن است" if enabled else "خاموش است"
    header = (
        f"🔔 <b>اعلان‌های شما</b>\n"
        f"اعلان روزانه فهرست کاستوم‌ها {status}.\n"
        "هر روز ساعت ۱۸:۰۰ تهران اگر کاستوم پیش‌رو باشد داخل ربات برایتان می‌آید.\n"
    )
    rows = (
        await db.scalars(
            select(Notification)
            .where(Notification.user_id == db_user.id)
            .order_by(Notification.created_at.desc())
            .limit(10)
        )
    ).all()
    text = header + ("\nهنوز اعلان ذخیره‌شده‌ای ندارید." if not rows else "\n" + "\n\n".join(
        f"<b>{esc(n.title)}</b>\n{esc(n.body)}" for n in rows
    ))
    await _show_panel(event, text, kb)


@router.callback_query(F.data == "notifs:toggle")
async def notifs_toggle(cb: CallbackQuery, db: AsyncSession, db_user: User):
    db_user.notification_enabled = not bool(db_user.notification_enabled)
    await db.flush()
    await notifs(cb, db, db_user)


@router.callback_query(F.data.startswith("reveal:"))
async def reveal(cb: CallbackQuery):
    await cb.answer(
        "ROOM ID و PASS فقط در پیام خصوصی ارسال می‌شود. اگر پیام را نگرفته‌اید واجد شرایط نبوده‌اید یا هنوز زمان ارسال نرسیده است.",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("rep:") & ~F.data.startswith("repr:"))
async def report_event(cb: CallbackQuery, db: AsyncSession, db_user: User):
    if not await _ensure_onboarding(cb.message, db_user, db):
        await cb.answer()
        return
    token = cb.data.split(":", 1)[1]
    e = await _event_by_token(db, token)
    if not e:
        await cb.answer("کاستوم یافت نشد", show_alert=True)
        return
    org = e.organizer
    if org and org.user_id == db_user.id:
        await cb.message.answer(
            f"گزارش کاستوم «{esc(e.title)}»\n"
            "برای کاستوم خودتان فقط می‌توانید چیتر را گزارش کنید.",
            reply_markup=report_reasons_kb(token, cheater_only=True),
        )
        await cb.answer()
        return
    await cb.message.answer(
        f"گزارش کاستوم «{esc(e.title)}»\nدلیل را انتخاب کنید:",
        reply_markup=report_reasons_kb(token),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("repr:"))
async def report_reason_chosen(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    rest = cb.data.split(":", 2)
    if len(rest) < 3:
        await cb.answer("نامعتبر", show_alert=True)
        return
    token, reason = rest[1], rest[2]
    e = await _event_by_token(db, token)
    if not e:
        await cb.answer("کاستوم یافت نشد", show_alert=True)
        return
    if reason == "other":
        await state.set_state(ReportSG.body)
        await state.update_data(event_token=token, reason=reason)
        await cb.message.answer("توضیح کوتاه بفرستید: چه اتفاقی افتاد؟")
        await cb.answer()
        return
    if reason == "cheater":
        if datetime.now(UTC) < e.starts_at:
            await cb.answer("کاستوم هنوز شروع نشده.", show_alert=True)
            return
        await state.set_state(ReportSG.body)
        await state.update_data(event_token=token, reason=reason)
        await cb.message.answer(
            "نام چیتر داخل Free Fire را بفرستید.\n"
            "همان نامی که در کاستوم دیدید."
        )
        await cb.answer()
        return
    report, err = await create_player_report(db, reporter=db_user, event=e, reason=reason)
    if err:
        await cb.answer(err, show_alert=True)
        return
    await _notify_report(cb.bot, db, report, e, db_user)
    await cb.message.answer("گزارش برای مالک ربات ثبت شد.")
    await cb.answer()


@router.message(ReportSG.body)
async def report_other_body(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    body = (message.text or "").strip()
    if body in labeled("لغو", "انصراف") | {"/cancel"}:
        await state.clear()
        await message.answer("لغو شد.", reply_markup=await menu_for(db, db_user))
        return
    data = await state.get_data()
    token = data.get("event_token")
    reason = data.get("reason") or "other"
    if reason == "cheater":
        if len(body) < 2:
            await message.answer("نام چیتر را بفرستید (حداقل ۲ حرف).")
            return
    elif len(body) < 5:
        await message.answer("کمی بیشتر توضیح بدهید (حداقل چند کلمه).")
        return
    e = await _event_by_token(db, token)
    await state.clear()
    if not e:
        await message.answer("کاستوم یافت نشد.")
        return
    report, err = await create_player_report(db, reporter=db_user, event=e, reason=reason, body=body)
    if err:
        await message.answer(err)
        return
    await _notify_report(message.bot, db, report, e, db_user)
    if reason == "cheater":
        await message.answer("گزارش چیتر برای مالک ربات و برگزارکننده کاستوم ثبت شد.")
        return
    await message.answer("گزارش برای مالک ربات ثبت شد.")


async def _notify_report(bot, db: AsyncSession, report, event: Event, reporter: User) -> None:
    org = event.organizer or await db.get(Organizer, event.organizer_id)
    org_user = await db.get(User, org.user_id) if org else None
    await notify_active_admins(
        bot,
        db,
        format_report_alert(
            event=event,
            reporter=reporter,
            reason=report.reason,
            body=report.body,
            organizer_user=org_user,
        ),
    )
    if report.reason != ReportReason.CHEATER or not org_user:
        return
    await notify_telegram_user(
        bot,
        org_user.telegram_id,
        format_cheater_alert_for_organizer(event=event, reporter=reporter, body=report.body),
    )


async def _notify_prize_vote(
    bot, db: AsyncSession, reporter: User, event: Event, prize: str, extra: str | None
) -> None:
    if prize not in {"yes", "no"}:
        return
    org = event.organizer or await db.get(Organizer, event.organizer_id)
    org_user = await db.get(User, org.user_id) if org else None
    paid = prize == "yes"
    if not paid:
        _report, err = await create_player_report(
            db,
            reporter=reporter,
            event=event,
            reason="unpaid_prize",
            body=(extra or "").strip() or "از نظر بازیکن: جایزه را نداد.",
        )
        if err and "قبلاً" not in (err or ""):
            pass
    await notify_active_admins(
        bot,
        db,
        format_prize_vote_alert(
            event=event,
            reporter=reporter,
            organizer_user=org_user,
            paid=paid,
            extra=extra,
        ),
    )


@router.callback_query(F.data.startswith("rvl:"))
async def list_reviews(cb: CallbackQuery, db: AsyncSession):
    token = cb.data.split(":", 1)[1]
    e = await _event_by_token(db, token)
    if not e:
        await cb.answer("یافت نشد", show_alert=True)
        return
    rows = await list_event_reviews(db, e.id)
    summary = await review_summary_for_event(db, e.id)
    org_sum = await review_summary_for_organizer(db, e.organizer_id)
    text = (
        f"نظرات کاستوم «{esc(e.title)}»\n"
        f"{format_rating_line(summary, prefix='این کاستوم')}\n"
        f"{format_rating_line(org_sum, prefix='سابقه برگزارکننده')}\n"
    )
    if not rows:
        text += "\nهنوز نظری نیست. اگر در این کاستوم بودید، «نظر و امتیاز» را بزنید."
    else:
        text += "\n" + "\n\n".join(format_review_item(r) for r in rows)
    await cb.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[ibtn("بازگشت به کاستوم", callback_data=f"ev:{token}", style=PRIMARY)]]
        ),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("rev:"))
async def start_review(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    if not await _ensure_onboarding(cb.message, db_user, db):
        await cb.answer()
        return
    token = cb.data.split(":", 1)[1]
    e = await _event_by_token(db, token)
    if not e:
        await cb.answer("یافت نشد", show_alert=True)
        return
    ok, err = await can_review(db, db_user, e)
    if not ok:
        await cb.answer(err or "نمی‌توانید نظر بدهید.", show_alert=True)
        return
    await state.set_state(ReviewSG.rating)
    await state.update_data(review_token=token)
    await cb.message.answer(
        f"به کاستوم «{esc(e.title)}» از ۱ تا ۵ ستاره بدهید.",
        reply_markup=review_stars_kb(token),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("rvs:"))
async def review_star(cb: CallbackQuery, state: FSMContext):
    rest = cb.data.split(":")
    if len(rest) < 3:
        await cb.answer("نامعتبر", show_alert=True)
        return
    token, raw = rest[1], rest[2]
    try:
        rating = int(raw)
    except ValueError:
        await cb.answer("نامعتبر", show_alert=True)
        return
    if rating not in {1, 2, 3, 4, 5}:
        await cb.answer("امتیاز باید ۱ تا ۵ باشد.", show_alert=True)
        return
    await state.set_state(ReviewSG.prize)
    await state.update_data(review_token=token, review_rating=rating)
    await cb.message.answer(
        "جایزه این کاستوم را به برنده دادند؟\n"
        "این جواب فقط برای مالک ربات می‌رود و بقیه بازیکن‌ها آن را نمی‌بینند.",
        reply_markup=review_prize_kb(token),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("rvp:"))
async def review_prize(cb: CallbackQuery, state: FSMContext):
    rest = cb.data.split(":")
    if len(rest) < 3:
        await cb.answer("نامعتبر", show_alert=True)
        return
    token, vote = rest[1], rest[2]
    await state.set_state(ReviewSG.comment)
    await state.update_data(review_token=token, review_prize=vote)
    await cb.message.answer(
        "اگر توضیحی برای نظر عمومی دارید در یک پیام بفرستید (امتیاز و متن برای همه دیده می‌شود).\n"
        "وضعیت جایزه را بقیه نمی‌بینند؛ همان فقط به مالک ربات می‌رود.\n"
        "اگر توضیح نمی‌خواهید، دکمه زیر را بزنید.",
        reply_markup=review_comment_kb(token),
    )
    await cb.answer()


@router.message(ReviewSG.rating)
@router.message(ReviewSG.prize)
async def review_use_buttons(message: Message):
    await message.answer("از دکمه‌های زیر پیام قبلی استفاده کنید، یا /cancel برای انصراف.")


@router.callback_query(F.data.startswith("rvn:"))
async def review_skip_comment(cb: CallbackQuery, state: FSMContext, db: AsyncSession, db_user: User):
    data = await state.get_data()
    token = cb.data.split(":", 1)[1]
    await _finish_review(cb.message, state, db, db_user, token, data, comment=None)
    await cb.answer()


@router.message(ReviewSG.comment)
async def review_comment(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    data = await state.get_data()
    token = data.get("review_token")
    await _finish_review(message, state, db, db_user, token, data, comment=message.text)


async def _finish_review(message, state: FSMContext, db: AsyncSession, db_user: User, token, data: dict, comment: str | None):
    e = await _event_by_token(db, token)
    rating = int(data.get("review_rating") or 0)
    prize = data.get("review_prize") or "unknown"
    await state.clear()
    if not e:
        await message.answer("کاستوم یافت نشد.")
        return
    _row, err = await create_review(
        db, user=db_user, event=e, rating=rating, prize_paid=prize, comment=comment
    )
    if err:
        await message.answer(err)
        return
    await _notify_prize_vote(message.bot, db, db_user, e, prize, comment)
    if prize in {"yes", "no"}:
        await message.answer(
            "نظر و امتیاز شما ثبت شد و برای بقیه دیده می‌شود.\n"
            "وضعیت جایزه فقط برای مالک ربات ارسال شد."
        )
    else:
        await message.answer("نظر و امتیاز شما ثبت شد و برای بقیه دیده می‌شود.")


@router.callback_query(F.data == "menu:home")
async def menu_home(cb: CallbackQuery, db: AsyncSession, db_user: User, state: FSMContext):
    await state.clear()
    await replace_callback_view(
        cb,
        "منوی اصلی آماده است.\nاز دکمه‌های پایین استفاده کنید. اگر منو باز است، با فلش کنار کادر پیام جمعش کنید.",
    )
