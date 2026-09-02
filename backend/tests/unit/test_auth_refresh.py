"""Refresh tokens were minted on every login but no endpoint could spend them,
so the web panel died silently 20 minutes after login."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.errors import UnauthorizedError
from app.core.security import create_refresh_token, decode_token
from app.models.admin import Admin, AdminSession
from app.services.auth import refresh_tokens, revoke_refresh
from tests.conftest import make_user


async def _admin_with_session(db):
    user = await db.run_sync(lambda s: make_user(s, 5001))
    admin = Admin(user_id=user.id, is_active=True, is_super_admin=True, password_hash="x")
    db.add(admin)
    await db.flush()
    jti = "jti-one"
    now = datetime.now(UTC)
    db.add(
        AdminSession(
            admin_id=admin.id,
            refresh_jti=jti,
            expires_at=now + timedelta(days=7),
            last_seen_at=now,
        )
    )
    await db.commit()
    return user, admin, create_refresh_token(str(user.id), jti)


@pytest.mark.asyncio
async def test_refresh_returns_a_new_pair_and_rotates_the_jti(async_db):
    db = async_db
    user, admin, token = await _admin_with_session(db)

    access, refresh = await refresh_tokens(db, token, "1.2.3.4", "agent")
    await db.commit()

    assert decode_token(access)["type"] == "access"
    assert decode_token(access)["sub"] == str(user.id)
    new_jti = decode_token(refresh)["jti"]
    assert new_jti != "jti-one", "the old refresh token must not stay valid"

    session = await db.scalar(select(AdminSession).where(AdminSession.admin_id == admin.id))
    assert session.refresh_jti == new_jti
    assert session.ip_address == "1.2.3.4"


@pytest.mark.asyncio
async def test_a_used_refresh_token_cannot_be_replayed(async_db):
    db = async_db
    _, _, token = await _admin_with_session(db)
    await refresh_tokens(db, token, None, None)
    await db.commit()

    with pytest.raises(UnauthorizedError):
        await refresh_tokens(db, token, None, None)


@pytest.mark.asyncio
async def test_revoked_session_stops_refreshing(async_db):
    db = async_db
    _, admin, token = await _admin_with_session(db)
    await revoke_refresh(db, token)
    await db.commit()

    with pytest.raises(UnauthorizedError):
        await refresh_tokens(db, token, None, None)


@pytest.mark.asyncio
async def test_expired_session_stops_refreshing(async_db):
    db = async_db
    _, admin, token = await _admin_with_session(db)
    session = await db.scalar(select(AdminSession).where(AdminSession.admin_id == admin.id))
    session.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db.commit()

    with pytest.raises(UnauthorizedError):
        await refresh_tokens(db, token, None, None)


@pytest.mark.asyncio
async def test_an_access_token_is_not_a_refresh_token(async_db):
    from app.core.security import create_access_token

    db = async_db
    user = await db.run_sync(lambda s: make_user(s, 5002))
    await db.commit()
    with pytest.raises(UnauthorizedError):
        await refresh_tokens(db, create_access_token(str(user.id)), None, None)


@pytest.mark.asyncio
async def test_garbage_token_is_rejected_not_crashed(async_db):
    with pytest.raises(UnauthorizedError):
        await refresh_tokens(async_db, "not-a-jwt", None, None)
