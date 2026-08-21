from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from app.core.config import get_settings
from app.locales import fa as T

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


async def setup_bot_profile(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="شروع مجدد"),
            BotCommand(command="customs", description="کاستوم‌های جایزه‌دار"),
            BotCommand(command="host", description="ثبت کاستوم"),
            BotCommand(command="winner", description="اعلام برنده"),
            BotCommand(command="help", description="راهنما و قوانین"),
            BotCommand(command="cancel", description="لغو عملیات جاری"),
        ]
    )
    await bot.set_my_description(T.INTRO)
    await bot.set_my_short_description(T.BOT_ABOUT)


def get_dispatcher() -> Dispatcher:
    global _dp
    if _dp is None:
        from aiogram.fsm.storage.redis import RedisStorage

        storage = RedisStorage.from_url(get_settings().redis_url)
        _dp = Dispatcher(storage=storage)
        from app.bot.handlers import setup_handlers
        from app.bot.middlewares.context import (
            BanMiddleware,
            DbSessionMiddleware,
            MaintenanceMiddleware,
            MenuResetMiddleware,
        )

        _dp.update.outer_middleware(DbSessionMiddleware())
        _dp.update.outer_middleware(MaintenanceMiddleware())
        _dp.message.middleware(BanMiddleware())
        _dp.callback_query.middleware(BanMiddleware())
        _dp.message.middleware(MenuResetMiddleware())
        _dp.errors.register(_bot_error_handler)
        setup_handlers(_dp)
    return _dp


async def _bot_error_handler(event) -> bool:
    from aiogram.types import ErrorEvent

    from app.core.errors import AppError
    from app.core.logging import get_logger

    log = get_logger("bot")
    update = event.update if isinstance(event, ErrorEvent) else None
    exc = event.exception if isinstance(event, ErrorEvent) else event
    if isinstance(exc, AppError):
        text = exc.message
    else:
        log.exception("unhandled_bot_error", error=str(exc))
        text = "خطایی رخ داد. دوباره تلاش کنید."
    if update is None:
        return True
    try:
        if update.callback_query:
            await update.callback_query.answer(text, show_alert=True)
        elif update.message:
            await update.message.answer(text)
    except Exception:
        log.exception("bot_error_notify_failed")
    return True
