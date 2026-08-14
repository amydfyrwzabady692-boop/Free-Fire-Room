from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage

from app.core.config import get_settings
from app.core.redis import get_redis

_bot: Bot | None = None
_dp: Dispatcher | None = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        settings = get_settings()
        if not settings.bot_token:
            raise RuntimeError("BOT_TOKEN is not configured")
        _bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    return _bot


def get_dispatcher() -> Dispatcher:
    global _dp
    if _dp is None:
        storage = RedisStorage.from_url(get_settings().redis_url)
        _dp = Dispatcher(storage=storage)
        from app.bot.handlers import setup_handlers
        from app.bot.middlewares.context import DbSessionMiddleware, BanMiddleware, MaintenanceMiddleware

        _dp.update.outer_middleware(DbSessionMiddleware())
        _dp.update.outer_middleware(MaintenanceMiddleware())
        _dp.message.middleware(BanMiddleware())
        _dp.callback_query.middleware(BanMiddleware())
        setup_handlers(_dp)
    return _dp
