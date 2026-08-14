from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.enums import EventStatus, EventVisibility, GameMode


class Event(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "events"

    public_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    organizer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizers.id", ondelete="RESTRICT"), index=True
    )
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    banner_file_id: Mapped[str | None] = mapped_column(String(256))
    banner_url: Mapped[str | None] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    registration_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    credentials_send_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Tehran", nullable=False)
    region: Mapped[str] = mapped_column(String(32), default="ME", nullable=False, index=True)
    game_mode: Mapped[str] = mapped_column(String(16), default=GameMode.SQUAD, nullable=False, index=True)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    waitlist_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), default=EventVisibility.PUBLIC, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=EventStatus.DRAFT, nullable=False, index=True)
    featured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    require_rules_accept: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    require_ff_player_id: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    require_profile_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    required_referrals: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rules_text: Mapped[str | None] = mapped_column(Text)
    winner_method: Mapped[str | None] = mapped_column(Text)
    custom_credentials_message: Mapped[str | None] = mapped_column(Text)
    reveal_button_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reveal_ttl_seconds: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    personalize_delivery: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reminder_offsets_minutes: Mapped[list | None] = mapped_column(JSONB, default=list)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    deep_link_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    prize_summary: Mapped[str | None] = mapped_column(Text)

    organizer: Mapped["Organizer"] = relationship(back_populates="events")
    channel: Mapped["Channel | None"] = relationship()
    prizes: Mapped[list[EventPrize]] = relationship(back_populates="event", cascade="all, delete-orphan")
    requirements: Mapped[list[EventRequirement]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    required_channels: Mapped[list[EventRequiredChannel]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    credentials: Mapped[RoomCredential | None] = relationship(back_populates="event", uselist=False)
    registrations: Mapped[list["Registration"]] = relationship(back_populates="event")


class EventPrize(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "event_prizes"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    place: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    estimated_value: Mapped[int | None] = mapped_column(Integer)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    event: Mapped[Event] = relationship(back_populates="prizes")


class EventRequirement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "event_requirements"
    __table_args__ = (UniqueConstraint("event_id", "requirement_type", "ref_id"),)

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    requirement_type: Mapped[str] = mapped_column(String(64), nullable=False)
    ref_id: Mapped[str | None] = mapped_column(String(64))
    config: Mapped[dict | None] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    label: Mapped[str | None] = mapped_column(String(256))

    event: Mapped[Event] = relationship(back_populates="requirements")


class EventRequiredChannel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "event_required_channels"
    __table_args__ = (UniqueConstraint("event_id", "channel_id"),)

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    event: Mapped[Event] = relationship(back_populates="required_channels")
    channel: Mapped["Channel"] = relationship()


class RoomCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "room_credentials"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), unique=True
    )
    room_id_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    room_password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_changed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    correction_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    event: Mapped[Event] = relationship(back_populates="credentials")


from app.models.channel import Channel  # noqa: E402
from app.models.organizer import Organizer  # noqa: E402
from app.models.registration import Registration  # noqa: E402
