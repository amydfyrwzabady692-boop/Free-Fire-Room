from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.enums import AnnouncementStatus


class CustomAnnouncement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "custom_announcements"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    channel_name: Mapped[str] = mapped_column(String(128), nullable=False)
    channel_username: Mapped[str | None] = mapped_column(String(64))
    channel_url: Mapped[str | None] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Tehran", nullable=False)
    prize_summary: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    extra_join_links: Mapped[list | None] = mapped_column(JSONB, default=list)
    region: Mapped[str] = mapped_column(String(32), default="ME", nullable=False)
    game_mode: Mapped[str] = mapped_column(String(16), default="squad", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=AnnouncementStatus.PUBLISHED, nullable=False, index=True)
    hidden_reason: Mapped[str | None] = mapped_column(Text)
    hidden_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))

    user: Mapped["User"] = relationship(foreign_keys=[user_id])


from app.models.user import User  # noqa: E402
