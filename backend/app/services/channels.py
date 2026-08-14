from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationAppError
from app.models.channel import Channel, ChannelOwnership, GlobalRequiredChannel
from app.services.audit import write_audit
from app.services.telegram_ops import inspect_bot_admin, user_is_channel_admin


async def upsert_channel_from_inspect(db: AsyncSession, result) -> Channel:
    if not result.ok or not result.chat_id:
        hint = {
            "bot_not_admin": "ربات باید در کانال ادمین باشد و بتواند وضعیت اعضا را ببیند.",
            "bot_forbidden": "ربات به این کانال دسترسی ندارد. ابتدا ربات را ادمین کنید.",
        }.get(result.error, "کانال قابل بررسی نیست. شناسه یا لینک را بررسی کنید و ربات را ادمین کنید.")
        raise ValidationAppError(result.error or "channel_unusable", hint)
    ch = await db.scalar(select(Channel).where(Channel.telegram_chat_id == result.chat_id))
    if ch is None:
        ch = Channel(
            telegram_chat_id=result.chat_id,
            username=result.username,
            title=result.title or str(result.chat_id),
            chat_type=result.chat_type or "channel",
            bot_is_admin=result.is_admin,
            bot_can_invite=result.can_invite,
            last_checked_at=datetime.now(UTC),
        )
        db.add(ch)
    else:
        ch.username = result.username
        ch.title = result.title or ch.title
        ch.bot_is_admin = result.is_admin
        ch.bot_can_invite = result.can_invite
        ch.last_checked_at = datetime.now(UTC)
        ch.last_check_error = None
        ch.deleted_at = None
    await db.flush()
    return ch


async def connect_organizer_channel(db: AsyncSession, bot, user, chat_ref: str | int) -> Channel:
    result = await inspect_bot_admin(bot, chat_ref)
    ch = await upsert_channel_from_inspect(db, result)
    is_admin, status = await user_is_channel_admin(bot, ch.telegram_chat_id, user.telegram_id)
    if not is_admin:
        raise ValidationAppError(
            "not_channel_admin",
            "شما Creator یا Administrator این کانال نیستید. با اکانت مدیر کانال وارد شوید.",
        )
    own = await db.scalar(
        select(ChannelOwnership).where(ChannelOwnership.channel_id == ch.id, ChannelOwnership.user_id == user.id)
    )
    now = datetime.now(UTC)
    if own is None:
        own = ChannelOwnership(
            channel_id=ch.id,
            user_id=user.id,
            telegram_status=status or "administrator",
            is_active=True,
            verified_at=now,
            last_verified_at=now,
        )
        db.add(own)
    else:
        own.is_active = True
        own.telegram_status = status or own.telegram_status
        own.last_verified_at = now
        own.verified_at = own.verified_at or now
    await write_audit(
        db,
        action="channel_connected",
        entity_type="channel",
        entity_id=ch.id,
        actor_id=user.id,
        extra={"telegram_chat_id": ch.telegram_chat_id},
    )
    await db.flush()
    return ch


async def add_global_required_channel(
    db: AsyncSession, bot, actor_id, chat_ref: str | int, *, scope: str = "all", sort_order: int = 0
) -> GlobalRequiredChannel:
    result = await inspect_bot_admin(bot, chat_ref)
    ch = await upsert_channel_from_inspect(db, result)
    existing = await db.scalar(
        select(GlobalRequiredChannel).where(GlobalRequiredChannel.channel_id == ch.id, GlobalRequiredChannel.scope == scope)
    )
    if existing:
        existing.is_active = True
        existing.sort_order = sort_order
        await db.flush()
        return existing
    row = GlobalRequiredChannel(
        channel_id=ch.id,
        scope=scope,
        sort_order=sort_order,
        is_active=True,
        applies_to_bot=True,
        applies_to_events=True,
    )
    db.add(row)
    await write_audit(
        db, action="global_channel_added", entity_type="global_required_channel", entity_id=row.id, actor_id=actor_id
    )
    await db.flush()
    return row


async def active_global_channels(db: AsyncSession, scope: str | None = None) -> list[GlobalRequiredChannel]:
    now = datetime.now(UTC)
    q = select(GlobalRequiredChannel).where(GlobalRequiredChannel.is_active.is_(True))
    rows = (await db.scalars(q)).all()
    out = []
    for r in rows:
        if r.starts_at and r.starts_at > now:
            continue
        if r.ends_at and r.ends_at < now:
            continue
        if scope and r.scope not in {"all", scope}:
            continue
        out.append(r)
    return sorted(out, key=lambda x: x.sort_order)
