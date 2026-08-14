from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.common import main_menu
from app.models.admin import Admin
from app.models.user import User


async def is_active_admin(db: AsyncSession, user: User | None) -> bool:
    if not user:
        return False
    admin = await db.scalar(select(Admin).where(Admin.user_id == user.id, Admin.is_active.is_(True)))
    return bool(admin)


async def menu_for(db: AsyncSession, user: User):
    return main_menu(admin=await is_active_admin(db, user))
