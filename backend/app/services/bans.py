from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.enums import BanScope, UserStatus
from app.core.errors import ForbiddenError
from app.models.user import Ban, User


def _ban_active_clause(now: datetime, scope: BanScope | None = None):
    clauses = [
        Ban.is_active.is_(True),
        or_(Ban.expires_at.is_(None), Ban.expires_at > now),
    ]
    if scope is not None:
        clauses.append(or_(Ban.scope == BanScope.BOT, Ban.scope == scope))
    return and_(*clauses)


async def active_bans(db: AsyncSession, user_id, scope: BanScope | None = None) -> list[Ban]:
    now = datetime.now(UTC)
    q = select(Ban).where(Ban.user_id == user_id, _ban_active_clause(now, scope))
    return list((await db.scalars(q)).all())


async def is_banned(db: AsyncSession, user: User, scope: BanScope = BanScope.BOT) -> Ban | None:
    if user.status == UserStatus.BANNED and scope == BanScope.BOT:
        bans = await active_bans(db, user.id, BanScope.BOT)
        return bans[0] if bans else Ban(scope=BanScope.BOT, reason="banned", is_active=True)
    bans = await active_bans(db, user.id, scope)
    return bans[0] if bans else None


async def assert_not_banned(db: AsyncSession, user: User, scope: BanScope = BanScope.BOT) -> None:
    ban = await is_banned(db, user, scope)
    if ban:
        until = f" تا {ban.expires_at.isoformat()}" if ban.expires_at else ""
        raise ForbiddenError(
            "user_banned",
            f"حساب شما محدود شده است{until}. دلیل: {ban.reason}",
        )


def is_banned_sync(db: Session, user: User, scope: BanScope = BanScope.BOT) -> Ban | None:
    now = datetime.now(UTC)
    q = select(Ban).where(Ban.user_id == user.id, _ban_active_clause(now, scope))
    return db.scalars(q).first()
