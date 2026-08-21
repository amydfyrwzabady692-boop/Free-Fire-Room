from aiogram import Dispatcher

from app.bot.handlers.admin import router as admin_router
from app.bot.handlers.announce import router as announce_router
from app.bot.handlers.organizer import router as organizer_router
from app.bot.handlers.player import router as player_router
from app.bot.handlers.winner import router as winner_router


def setup_handlers(dp: Dispatcher) -> None:
    dp.include_router(admin_router)
    dp.include_router(announce_router)
    dp.include_router(organizer_router)
    dp.include_router(winner_router)
    dp.include_router(player_router)
