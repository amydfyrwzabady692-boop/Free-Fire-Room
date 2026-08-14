from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import AnnouncementStatus, BanScope
from app.core.errors import ForbiddenError, ValidationAppError
from app.models.announcement import CustomAnnouncement
from app.models.user import User
from app.services.audit import write_audit
from app.services.bans import assert_not_banned
from app.services.settings import get_setting


async def create_announcement(db: AsyncSession, user: User, data: dict) -> CustomAnnouncement:
    await assert_not_banned(db, user, BanScope.ORGANIZE)
    max_per_day = int(await get_setting(db, "max_announcements_per_day", 5))
    since = datetime.now(UTC) - timedelta(hours=24)
    count = await db.scalar(
        select(func.count())
        .select_from(CustomAnnouncement)
        .where(
            CustomAnnouncement.user_id == user.id,
            CustomAnnouncement.created_at >= since,
            CustomAnnouncement.status != AnnouncementStatus.DELETED,
        )
    )
    if int(count or 0) >= max_per_day:
        raise ForbiddenError("announce_quota", f"سقف اطلاع‌رسانی در ۲۴ ساعت {max_per_day} مورد است.")

    starts_at = data["starts_at"]
    now = datetime.now(UTC)
    if starts_at <= now + timedelta(minutes=5):
        raise ValidationAppError("starts_too_soon", "زمان کاستوم باید حداقل ۵ دقیقه بعد باشد.")
    if starts_at > now + timedelta(days=14):
        raise ValidationAppError("starts_too_far", "زمان اطلاع‌رسانی حداکثر ۱۴ روز جلوتر است.")

    channel_name = (data.get("channel_name") or "").strip()
    if len(channel_name) < 2:
        raise ValidationAppError("channel_name", "نام کانال را وارد کنید.")

    title = (data.get("title") or "").strip() or f"کاستوم {channel_name}"
    row = CustomAnnouncement(
        user_id=user.id,
        title=title[:160],
        channel_name=channel_name[:128],
        channel_username=data.get("channel_username"),
        channel_url=data.get("channel_url"),
        starts_at=starts_at,
        timezone=data.get("timezone") or "Asia/Tehran",
        prize_summary=data.get("prize_summary"),
        description=data.get("description"),
        extra_join_links=data.get("extra_join_links") or [],
        region=data.get("region") or "ME",
        game_mode=data.get("game_mode") or "squad",
        status=AnnouncementStatus.PUBLISHED,
    )
    db.add(row)
    await write_audit(
        db,
        action="announcement_created",
        entity_type="announcement",
        entity_id=row.id,
        actor_id=user.id,
        extra={"channel": channel_name},
    )
    await db.flush()
    return row


async def list_upcoming_announcements(db: AsyncSession, *, limit: int = 20) -> list[CustomAnnouncement]:
    now = datetime.now(UTC)
    rows = (
        await db.scalars(
            select(CustomAnnouncement)
            .where(
                CustomAnnouncement.status == AnnouncementStatus.PUBLISHED,
                CustomAnnouncement.starts_at >= now,
            )
            .options(selectinload(CustomAnnouncement.user))
            .order_by(CustomAnnouncement.starts_at.asc())
            .limit(limit)
        )
    ).all()
    return list(rows)


async def hide_announcement(db: AsyncSession, row: CustomAnnouncement, actor_id, reason: str = "hidden") -> None:
    row.status = AnnouncementStatus.HIDDEN
    row.hidden_by = actor_id
    row.hidden_reason = reason
    await write_audit(
        db,
        action="announcement_hidden",
        entity_type="announcement",
        entity_id=row.id,
        actor_id=actor_id,
        extra={"reason": reason},
    )
    await db.flush()


async def delete_own_announcement(db: AsyncSession, row: CustomAnnouncement, user: User) -> None:
    if row.user_id != user.id:
        raise ForbiddenError("not_owner", "فقط اعلام‌کننده می‌تواند این مورد را حذف کند.")
    row.status = AnnouncementStatus.DELETED
    await db.flush()
