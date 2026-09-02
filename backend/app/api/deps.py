from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import decode_token
from app.core.session import get_db
from app.models.admin import Admin
from app.models.organizer import Organizer
from app.models.user import User, UserRole
from app.services.bans import assert_not_banned
from app.core.enums import BanScope, OrganizerStatus

settings = get_settings()


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> User:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
    if not token:
        token = request.cookies.get("access_token")
    if not token:
        raise UnauthorizedError()
    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise UnauthorizedError("invalid_token", "نشست نامعتبر است.") from exc
    if payload.get("type") != "access":
        raise UnauthorizedError("invalid_token", "نشست نامعتبر است.")
    user = await db.scalar(
        select(User)
        .where(User.id == UUID(payload["sub"]), User.deleted_at.is_(None))
        .options(selectinload(User.roles).selectinload(UserRole.role), selectinload(User.profile), selectinload(User.organizer))
    )
    if not user:
        raise UnauthorizedError("user_not_found", "کاربر یافت نشد.")
    await assert_not_banned(db, user, BanScope.BOT)
    request.state.user = user
    return user


async def get_current_admin(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Admin:
    admin = await db.scalar(select(Admin).where(Admin.user_id == user.id, Admin.is_active.is_(True)))
    if not admin:
        raise ForbiddenError("admin_required", "فقط مدیران به این بخش دسترسی دارند.")
    return admin


async def get_super_admin(admin: Admin = Depends(get_current_admin)) -> Admin:
    if not admin.is_super_admin:
        raise ForbiddenError("super_admin_required", "فقط مالک اصلی ربات به این بخش دسترسی دارد.")
    return admin


async def get_organizer(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Organizer:
    org = await db.scalar(select(Organizer).where(Organizer.user_id == user.id))
    if not org or org.status not in {OrganizerStatus.APPROVED, OrganizerStatus.PENDING}:
        raise ForbiddenError("organizer_required", "حساب برگزارکننده فعال نیست.")
    if org.status != OrganizerStatus.APPROVED:
        raise ForbiddenError("organizer_not_approved", "حساب برگزارکننده هنوز تأیید نشده است.")
    return org


def require_permission(*codes: str):
    async def _dep(user: User = Depends(get_current_user), admin: Admin = Depends(get_current_admin)) -> User:
        if admin.is_super_admin:
            return user
        overrides = admin.permission_overrides or {}
        granted = set(overrides.get("allow") or [])
        denied = set(overrides.get("deny") or [])
        for code in codes:
            if code in denied:
                raise ForbiddenError("permission_denied", "سطح دسترسی شما برای این عملیات کافی نیست.")
            if granted and code not in granted:
                raise ForbiddenError("permission_denied", "سطح دسترسی شما برای این عملیات کافی نیست.")
        return user

    return _dep
