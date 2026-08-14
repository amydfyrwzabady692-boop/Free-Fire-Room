from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pyotp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ForbiddenError, RateLimitError, UnauthorizedError, ValidationAppError
from app.core.rate_limit import hit_rate_limit
from app.core.redis import get_redis
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decrypt_secret,
    encrypt_secret,
    generate_otp,
    hash_password,
    verify_password,
    verify_telegram_login,
)
from app.models.admin import Admin, AdminSession
from app.models.user import User
from app.services.audit import write_audit
from app.services.users import get_by_telegram

settings = get_settings()


async def issue_otp(telegram_id: int) -> str:
    await hit_rate_limit(f"rl:otp:{telegram_id}", settings.rate_limit_login_per_minute)
    code = generate_otp()
    redis = get_redis()
    await redis.setex(f"otp:{telegram_id}", settings.otp_expire_seconds, code)
    await redis.setex(f"otp_tries:{telegram_id}", settings.otp_expire_seconds, "0")
    return code


async def verify_otp(telegram_id: int, code: str) -> bool:
    redis = get_redis()
    tries_raw = await redis.get(f"otp_tries:{telegram_id}")
    tries = int(tries_raw or 0)
    if tries >= settings.otp_max_attempts:
        raise RateLimitError("otp_locked", "تعداد تلاش ورود بیش از حد است. چند دقیقه بعد تلاش کنید.")
    expected = await redis.get(f"otp:{telegram_id}")
    if not expected or expected != code.strip():
        await redis.incr(f"otp_tries:{telegram_id}")
        return False
    await redis.delete(f"otp:{telegram_id}")
    return True


async def login_super_admin(
    db: AsyncSession,
    *,
    telegram_id: int,
    password: str,
    totp_code: str | None,
    ip: str | None,
    user_agent: str | None,
) -> tuple[str, str, AdminSession]:
    await hit_rate_limit(f"rl:login:{ip or telegram_id}", settings.rate_limit_login_per_minute)
    user = await get_by_telegram(db, telegram_id)
    if not user:
        raise UnauthorizedError("invalid_credentials", "ورود نامعتبر است.")
    admin = await db.scalar(select(Admin).where(Admin.user_id == user.id, Admin.is_active.is_(True)))
    if not admin or not admin.password_hash:
        raise UnauthorizedError("invalid_credentials", "ورود نامعتبر است.")
    now = datetime.now(UTC)
    if admin.locked_until and admin.locked_until > now:
        raise ForbiddenError("admin_locked", "حساب مدیریت موقتاً قفل شده است.")
    if not verify_password(password, admin.password_hash):
        admin.failed_login_count += 1
        if admin.failed_login_count >= 8:
            admin.locked_until = now + timedelta(minutes=15)
        await db.flush()
        raise UnauthorizedError("invalid_credentials", "ورود نامعتبر است.")
    if admin.totp_enabled:
        if not totp_code or not admin.totp_secret_encrypted:
            raise ValidationAppError("totp_required", "کد دومرحله‌ای لازم است.")
        secret = decrypt_secret(admin.totp_secret_encrypted)
        if not pyotp.TOTP(secret).verify(totp_code, valid_window=1):
            raise UnauthorizedError("invalid_totp", "کد دومرحله‌ای نادرست است.")
    admin.failed_login_count = 0
    admin.last_login_at = now
    jti = uuid4().hex
    session = AdminSession(
        admin_id=admin.id,
        refresh_jti=jti,
        ip_address=ip,
        user_agent=user_agent,
        expires_at=now + timedelta(days=settings.refresh_token_expire_days),
        last_seen_at=now,
    )
    db.add(session)
    await write_audit(
        db,
        action="admin_login",
        entity_type="admin",
        entity_id=admin.id,
        actor_id=user.id,
        ip_address=ip,
        user_agent=user_agent,
    )
    await db.flush()
    access = create_access_token(
        str(user.id),
        extra={"admin_id": str(admin.id), "super": admin.is_super_admin, "tg": user.telegram_id},
    )
    refresh = create_refresh_token(str(user.id), jti)
    return access, refresh, session


async def login_telegram_widget(db: AsyncSession, payload: dict, ip: str | None, user_agent: str | None) -> tuple[str, str]:
    if not verify_telegram_login(payload):
        raise UnauthorizedError("invalid_telegram_login", "ورود تلگرام معتبر نیست.")
    auth_date = int(payload.get("auth_date") or 0)
    if datetime.now(UTC).timestamp() - auth_date > 86400:
        raise UnauthorizedError("stale_telegram_login", "ورود تلگرام منقضی شده است.")
    user = await get_by_telegram(db, int(payload["id"]))
    if not user:
        raise UnauthorizedError("user_not_found", "ابتدا ربات را استارت کنید.")
    return await _issue_user_tokens(db, user, ip, user_agent)


async def login_with_otp(db: AsyncSession, telegram_id: int, code: str, ip: str | None, ua: str | None) -> tuple[str, str]:
    if not await verify_otp(telegram_id, code):
        raise UnauthorizedError("invalid_otp", "کد یک‌بارمصرف نادرست است.")
    user = await get_by_telegram(db, telegram_id)
    if not user:
        raise UnauthorizedError("user_not_found", "کاربر یافت نشد.")
    return await _issue_user_tokens(db, user, ip, ua)


async def _issue_user_tokens(db: AsyncSession, user: User, ip: str | None, ua: str | None) -> tuple[str, str]:
    admin = await db.scalar(select(Admin).where(Admin.user_id == user.id, Admin.is_active.is_(True)))
    extra = {"tg": user.telegram_id}
    if admin:
        extra["admin_id"] = str(admin.id)
        extra["super"] = admin.is_super_admin
        jti = uuid4().hex
        now = datetime.now(UTC)
        db.add(
            AdminSession(
                admin_id=admin.id,
                refresh_jti=jti,
                ip_address=ip,
                user_agent=ua,
                expires_at=now + timedelta(days=settings.refresh_token_expire_days),
                last_seen_at=now,
            )
        )
        refresh = create_refresh_token(str(user.id), jti)
    else:
        refresh = create_refresh_token(str(user.id), uuid4().hex)
    access = create_access_token(str(user.id), extra=extra)
    await db.flush()
    return access, refresh


async def enable_totp(db: AsyncSession, admin: Admin) -> str:
    secret = pyotp.random_base32()
    admin.totp_secret_encrypted = encrypt_secret(secret)
    admin.totp_enabled = True
    await db.flush()
    return secret
