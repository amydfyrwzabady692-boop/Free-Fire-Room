from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import RoleName
from app.models.user import Role, User, UserProfile, UserRole


async def get_by_telegram(db: AsyncSession, telegram_id: int) -> User | None:
    return await db.scalar(
        select(User)
        .where(User.telegram_id == telegram_id, User.deleted_at.is_(None))
        .options(
            selectinload(User.profile),
            selectinload(User.roles).selectinload(UserRole.role),
            selectinload(User.organizer),
        )
    )


async def upsert_from_telegram(db: AsyncSession, tg_user) -> User:
    user = await get_by_telegram(db, tg_user.id)
    now = datetime.now(UTC)
    if user is None:
        user = User(
            telegram_id=tg_user.id,
            username=getattr(tg_user, "username", None),
            first_name=getattr(tg_user, "first_name", None),
            last_name=getattr(tg_user, "last_name", None),
            language="fa",
            timezone="Asia/Tehran",
            last_seen_at=now,
        )
        db.add(user)
        await db.flush()
        db.add(UserProfile(user_id=user.id))
        role = await db.scalar(select(Role).where(Role.name == RoleName.PLAYER))
        if role:
            db.add(UserRole(user_id=user.id, role_id=role.id))
        await db.flush()
        loaded = await get_by_telegram(db, tg_user.id)
        return loaded or user
    user.username = getattr(tg_user, "username", None)
    user.first_name = getattr(tg_user, "first_name", None)
    user.last_name = getattr(tg_user, "last_name", None)
    user.last_seen_at = now
    user.is_bot_blocked = False
    await db.flush()
    return user


async def user_permissions(db: AsyncSession, user: User) -> set[str]:
    await db.refresh(user, ["roles"])
    codes: set[str] = set()
    for ur in user.roles:
        role = ur.role
        for rp in role.permissions:
            codes.add(rp.permission.code)
    return codes


async def has_role(user: User, name: str) -> bool:
    return any(ur.role.name == name for ur in user.roles)
