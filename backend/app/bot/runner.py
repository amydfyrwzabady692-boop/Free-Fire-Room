from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.bot.loader import get_bot, get_dispatcher

log = get_logger("bot")


async def _run_polling() -> None:
    bot = get_bot()
    dp = get_dispatcher()
    await bot.delete_webhook(drop_pending_updates=False)
    log.info("bot_polling_start")
    await dp.start_polling(bot)


async def _set_webhook() -> None:
    settings = get_settings()
    bot = get_bot()
    url = settings.public_base_url.rstrip("/") + settings.webhook_path
    await bot.set_webhook(url, secret_token=settings.webhook_secret, drop_pending_updates=False)
    log.info("webhook_set", url=url)


def main() -> None:
    configure_logging()
    logging.getLogger("aiogram").setLevel(logging.INFO)
    settings = get_settings()
    if settings.telegram_mode == "webhook":
        asyncio.run(_set_webhook())
        return
    asyncio.run(_run_polling())


if __name__ == "__main__":
    main()
