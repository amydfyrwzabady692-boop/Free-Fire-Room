from __future__ import annotations

from app.core.enums import NotificationKind
from app.models.jobs import Notification, NotificationPreference
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session


MANDATORY = {NotificationKind.SECURITY, NotificationKind.EVENT_CHANGED, NotificationKind.EVENT_CANCELLED, NotificationKind.ROOM_CREDENTIALS}


async def is_enabled(db: AsyncSession, user_id, kind: str) -> bool:
    if kind in MANDATORY:
        return True
    pref = await db.scalar(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user_id, NotificationPreference.kind == kind
        )
    )
    if pref is None:
        return True
    return pref.enabled


async def notify(db: AsyncSession, user_id, kind: str, title: str, body: str, event_id=None, mandatory: bool = False) -> Notification | None:
    if not mandatory and not await is_enabled(db, user_id, kind):
        return None
    row = Notification(
        user_id=user_id,
        kind=kind,
        title=title,
        body=body,
        is_mandatory=mandatory or kind in MANDATORY,
        event_id=event_id,
    )
    db.add(row)
    await db.flush()
    return row


def notify_sync(db: Session, user_id, kind: str, title: str, body: str, event_id=None) -> Notification:
    row = Notification(user_id=user_id, kind=kind, title=title, body=body, event_id=event_id)
    db.add(row)
    db.flush()
    return row
