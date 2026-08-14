from __future__ import annotations

from datetime import UTC, datetime

from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.common import membership_kb, tos_kb
from app.locales import fa as T
from app.models.channel import Channel
from app.models.user import User
from app.services.channels import active_global_channels
from app.services.referrals import validate_pending_referrals
from app.services.telegram_ops import get_membership


def target_message(event: Message | CallbackQuery) -> Message:
    return event.message if isinstance(event, CallbackQuery) else event


async def missing_global_memberships(db: AsyncSession, bot, user: User):
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


async def ensure_onboarding(message: Message, user: User, db: AsyncSession) -> bool:
    if not user.tos_accepted_at:
        await message.answer(T.TOS, reply_markup=tos_kb())
        return False
    missing = await missing_global_memberships(db, message.bot, user)
    if missing:
        buttons = []
        for ch, _ in missing:
            url = f"https://t.me/{ch.username}" if ch.username else ch.invite_link
            buttons.append((f"عضویت در {ch.title}", url or "https://t.me"))
        await message.answer(
            "برای استارت و استفاده از ربات باید در کانال‌های مالک ربات عضو شوید، سپس «بررسی مجدد عضویت» را بزنید.",
            reply_markup=membership_kb(buttons),
        )
        return False
    if not user.onboarding_completed_at:
        user.onboarding_completed_at = datetime.now(UTC)
        await validate_pending_referrals(db, user)
        await db.flush()
    return True
