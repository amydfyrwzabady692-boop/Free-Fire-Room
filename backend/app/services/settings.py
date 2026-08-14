from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.admin import SystemSetting

DEFAULTS: dict[str, Any] = {
    "event_approval_required": True,
    "max_events_per_organizer": 10,
    "max_required_channels_per_event": 5,
    "max_required_referrals": 20,
    "maintenance_mode": False,
    "default_timezone": "Asia/Tehran",
    "support_contact": "@support",
    "reminder_offsets_minutes": [60, 15],
    "feature_flags": {
        "waitlist": True,
        "reveal_button": True,
        "organizer_panel": True,
        "captcha_on_suspicious": True,
    },
    "data_retention": {
        "room_credentials_days": 7,
        "audit_days": 365,
        "delivery_days": 90,
    },
    "new_user_referral_hours": 24,
    "min_account_age_hours_for_referral": 0,
}


async def get_setting(db: AsyncSession, key: str, default: Any = None) -> Any:
    row = await db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    if row is None:
        if default is not None:
            return default
        return DEFAULTS.get(key)
    return row.value.get("v", row.value)


async def set_setting(db: AsyncSession, key: str, value: Any, updated_by=None, description: str | None = None) -> SystemSetting:
    row = await db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    wrapped = value if isinstance(value, dict) and "v" in value else {"v": value}
    if row is None:
        row = SystemSetting(key=key, value=wrapped, description=description, updated_by=updated_by)
        db.add(row)
    else:
        row.value = wrapped
        row.updated_by = updated_by
        if description:
            row.description = description
    await db.flush()
    return row


async def all_settings(db: AsyncSession) -> dict[str, Any]:
    rows = (await db.scalars(select(SystemSetting))).all()
    data = dict(DEFAULTS)
    env = get_settings()
    data["event_approval_required"] = env.event_approval_required
    data["max_events_per_organizer"] = env.max_events_per_organizer
    data["max_required_channels_per_event"] = env.max_required_channels_per_event
    data["max_required_referrals"] = env.max_required_referrals
    data["maintenance_mode"] = env.maintenance_mode
    for row in rows:
        data[row.key] = row.value.get("v", row.value)
    return data


def get_setting_sync(db: Session, key: str, default: Any = None) -> Any:
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    if row is None:
        return DEFAULTS.get(key) if default is None else default
    return row.value.get("v", row.value)
