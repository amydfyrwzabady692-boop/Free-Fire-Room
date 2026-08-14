from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.enums import OrganizerStatus


class Organizer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizers"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default=OrganizerStatus.PENDING, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(128))
    bio: Mapped[str | None] = mapped_column(Text)
    verified_badge: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    trust_score: Mapped[float] = mapped_column(Float, default=50.0, nullable=False)
    max_events: Mapped[int | None] = mapped_column(Integer)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="organizer")
    events: Mapped[list["Event"]] = relationship(back_populates="organizer")
    trust_events: Mapped[list["OrganizerTrustEvent"]] = relationship(back_populates="organizer")


class OrganizerTrustEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizer_trust_events"

    organizer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizers.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    delta: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    related_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))

    organizer: Mapped[Organizer] = relationship(back_populates="trust_events")


from app.models.event import Event  # noqa: E402
from app.models.user import User  # noqa: E402
