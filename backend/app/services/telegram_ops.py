from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.enums import ChatMemberStatus

ADMIN_STATUSES = {
    ChatMemberStatus.CREATOR,
    ChatMemberStatus.ADMINISTRATOR,
}
MEMBER_STATUSES = {
    ChatMemberStatus.CREATOR,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.RESTRICTED,
}


@dataclass
class MembershipResult:
    ok: bool
    status: str | None
    error: str | None = None


@dataclass
class BotAdminResult:
    ok: bool
    is_admin: bool
    can_invite: bool
    error: str | None = None
    title: str | None = None
    username: str | None = None
    chat_id: int | None = None
    chat_type: str | None = None


async def get_membership(bot: Bot, chat_id: int, user_id: int) -> MembershipResult:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        status = member.status
        if status == ChatMemberStatus.RESTRICTED and not getattr(member, "is_member", True):
            return MembershipResult(False, status)
        return MembershipResult(status in MEMBER_STATUSES, status)
    except TelegramForbiddenError:
        return MembershipResult(False, None, "bot_not_admin")
    except TelegramBadRequest as exc:
        return MembershipResult(False, None, str(exc))


async def inspect_bot_admin(bot: Bot, chat_id: int | str) -> BotAdminResult:
    try:
        chat = await bot.get_chat(chat_id)
        me = await bot.get_me()
        member = await bot.get_chat_member(chat.id, me.id)
        is_admin = member.status in ADMIN_STATUSES
        can_invite = False
        if member.status == ChatMemberStatus.CREATOR:
            can_invite = True
        elif member.status == ChatMemberStatus.ADMINISTRATOR:
            can_invite = bool(getattr(member, "can_invite_users", False) or getattr(member, "can_manage_chat", False))
        username = getattr(chat, "username", None)
        return BotAdminResult(
            ok=is_admin,
            is_admin=is_admin,
            can_invite=can_invite,
            title=chat.title,
            username=username,
            chat_id=chat.id,
            chat_type=chat.type,
            error=None if is_admin else "bot_not_admin",
        )
    except TelegramForbiddenError:
        return BotAdminResult(False, False, False, error="bot_forbidden")
    except TelegramBadRequest as exc:
        return BotAdminResult(False, False, False, error=str(exc))


async def user_is_channel_admin(bot: Bot, chat_id: int, user_id: int) -> tuple[bool, str | None]:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if member.status in ADMIN_STATUSES:
            return True, member.status
        return False, member.status
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
