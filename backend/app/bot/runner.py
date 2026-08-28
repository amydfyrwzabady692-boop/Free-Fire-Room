from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.bot.loader import get_bot, get_dispatcher, setup_bot_profile

log = get_logger("bot")


async def _run_scheduled_jobs() -> None:
    from datetime import UTC, datetime

    from app.workers.tasks import (
        dispatch_due_jobs,
        purge_old_credentials,
        recheck_channel_admin,
        send_daily_custom_digest,
    )

    settings = get_settings()
    tick = max(15, settings.job_dispatch_interval_seconds)
    last_recheck_hour = None
    last_purge_day = None
    last_digest_day = None
    while True:
        try:
            await asyncio.to_thread(dispatch_due_jobs.run)
        except Exception:
            log.exception("dispatch_due_jobs_failed")
        now = datetime.now(UTC)
        if now.minute == 15 and last_recheck_hour != now.hour:
            last_recheck_hour = now.hour
            try:
                await asyncio.to_thread(recheck_channel_admin.run)
            except Exception:
                log.exception("recheck_channel_admin_failed")
        if now.hour == 3 and 10 <= now.minute < 12 and last_purge_day != now.date():
            last_purge_day = now.date()
            try:
                await asyncio.to_thread(purge_old_credentials.run)
            except Exception:
                log.exception("purge_old_credentials_failed")
        if now.hour == 14 and 30 <= now.minute < 32 and last_digest_day != now.date():
            last_digest_day = now.date()
            try:
                await asyncio.to_thread(send_daily_custom_digest.run)
            except Exception:
                log.exception("daily_digest_failed")
        await asyncio.sleep(tick)


async def _run_polling() -> None:
    bot = get_bot()
    dp = get_dispatcher()
    await bot.delete_webhook(drop_pending_updates=False)
    try:
        await setup_bot_profile(bot)
    except Exception:
        log.exception("bot_profile_setup_failed")
    jobs = asyncio.create_task(_run_scheduled_jobs())
    log.info("bot_polling_start")
    try:
        await dp.start_polling(bot)
    finally:
        jobs.cancel()
        try:
            await jobs
        except asyncio.CancelledError:
            pass


async def _set_webhook() -> None:
    settings = get_settings()
    bot = get_bot()
    url = settings.public_base_url.rstrip("/") + settings.webhook_path
    await bot.set_webhook(url, secret_token=settings.webhook_secret, drop_pending_updates=False)
    await setup_bot_profile(bot)
    log.info("webhook_set", url=url)


@asynccontextmanager
async def run_bot_services():
    settings = get_settings()
    if not settings.bot_token:
        yield
        return

    polling_task: asyncio.Task | None = None
    jobs_task: asyncio.Task | None = None
    try:
        if settings.telegram_mode == "webhook":
            await _set_webhook()
            jobs_task = asyncio.create_task(_run_scheduled_jobs())
        else:
            polling_task = asyncio.create_task(_run_polling())
        yield
    finally:
        for task in (polling_task, jobs_task):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def main() -> None:
    configure_logging()
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    settings = get_settings()
    if settings.telegram_mode == "webhook":
        asyncio.run(_set_webhook())
        return
    asyncio.run(_run_polling())


if __name__ == "__main__":
    main()
