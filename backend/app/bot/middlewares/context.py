from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update, User as TgUser

from app.core.config import get_settings
from app.core.enums import BanScope
from app.core.session import SessionLocal
from app.services.bans import is_banned
from app.services.settings import get_setting
from app.services.users import upsert_from_telegram


class DbSessionMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable, event: TelegramObject, data: dict[str, Any]) -> Any:
        async with SessionLocal() as session:
            data["db"] = session
            tg_user: TgUser | None = data.get("event_from_user")
            if tg_user is None and isinstance(event, Update):
                for attr in ("message", "callback_query", "edited_message", "my_chat_member", "inline_query"):
                    obj = getattr(event, attr, None)
                    if obj is not None and getattr(obj, "from_user", None):
                        tg_user = obj.from_user
                        break
            if tg_user and not tg_user.is_bot:
                user = await upsert_from_telegram(session, tg_user)
                data["db_user"] = user
                await session.commit()
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise


class BanMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable, event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("db_user")
        db = data.get("db")
        if user and db:
            ban = await is_banned(db, user, BanScope.BOT)
            if ban:
                from aiogram.types import CallbackQuery, Message

                text = f"حساب شما محدود شده است.\nدلیل: {ban.reason}"
                if isinstance(event, Message):
                    await event.answer(text)
                elif isinstance(event, CallbackQuery):
                    await event.answer(text, show_alert=True)
                return None
        return await handler(event, data)


class MaintenanceMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable, event: TelegramObject, data: dict[str, Any]) -> Any:
        db = data.get("db")
        if db:
            on = await get_setting(db, "maintenance_mode", get_settings().maintenance_mode)
            user = data.get("db_user")
            is_admin = False
            if user:
                from app.models.admin import Admin
                from sqlalchemy import select

                admin = await db.scalar(select(Admin).where(Admin.user_id == user.id, Admin.is_active.is_(True)))
                is_admin = bool(admin)
            if on and not is_admin:
                from aiogram.types import Message

                if isinstance(event, Message):
                    await event.answer("ربات موقتاً در حال تعمیرات است. کمی بعد دوباره تلاش کنید.")
                return None
        return await handler(event, data)
