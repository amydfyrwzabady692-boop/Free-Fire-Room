from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.enums import GlobalChannelScope


class Channel(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "channels"

    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    invite_link: Mapped[str | None] = mapped_column(Text)
    chat_type: Mapped[str] = mapped_column(String(32), default="channel", nullable=False)
    bot_is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    bot_can_invite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_check_error: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    ownerships: Mapped[list[ChannelOwnership]] = relationship(back_populates="channel")


class ChannelOwnership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "channel_ownerships"
    __table_args__ = (UniqueConstraint("channel_id", "user_id"),)

    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    telegram_status: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    channel: Mapped[Channel] = relationship(back_populates="ownerships")


class GlobalRequiredChannel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "global_required_channels"

    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String(32), default=GlobalChannelScope.ALL, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applies_to_bot: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    applies_to_events: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    channel: Mapped[Channel] = relationship()
