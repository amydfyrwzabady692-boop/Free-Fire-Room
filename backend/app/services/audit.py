from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.admin import AuditLog

log = get_logger(__name__)

SENSITIVE_FIELDS = {
    "room_id",
    "room_password",
    "password",
    "room_id_encrypted",
    "room_password_encrypted",
    "totp_secret_encrypted",
    "password_hash",
    "bot_token",
    "otp",
}


def _sanitize(data: dict | None) -> dict | None:
    if not data:
        return data
    out = {}
    for k, v in data.items():
        if k.lower() in SENSITIVE_FIELDS or "password" in k.lower() or "secret" in k.lower():
            out[k] = "[REDACTED]"
        elif isinstance(v, dict):
            out[k] = _sanitize(v)
        else:
            out[k] = v
    return out


async def write_audit(
    db: AsyncSession,
    *,
    action: str,
    entity_type: str,
    entity_id: str | uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    actor_telegram_id: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    correlation_id: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
    extra: dict | None = None,
) -> None:
    extra_data = dict(extra or {})
    if actor_telegram_id is not None:
        extra_data.setdefault("telegram_id", actor_telegram_id)
    row = AuditLog(
        actor_id=actor_id,
        actor_telegram_id=actor_telegram_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        ip_address=ip_address,
        user_agent=user_agent,
        correlation_id=correlation_id,
        before=_sanitize(before),
        after=_sanitize(after),
        extra=_sanitize(extra_data) or None,
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except Exception:
        log.exception("audit_write_failed", action=action, entity_type=entity_type)
        return
    log.info("audit", action=action, entity_type=entity_type, entity_id=str(entity_id))
