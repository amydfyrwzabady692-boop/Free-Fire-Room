from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.enums import RegistrationStatus, RequirementStatus


class Registration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "registrations"
    __table_args__ = (
        UniqueConstraint("event_id", "user_id", name="uq_registrations_event_user"),
        Index("ix_registrations_event_status", "event_id", "status"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default=RegistrationStatus.PENDING, nullable=False, index=True
    )
    waitlist_position: Mapped[int | None] = mapped_column(Integer)
    conditions_met_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ineligible_reason: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(64))
    rules_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_requirement_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    event: Mapped["Event"] = relationship(back_populates="registrations")
    user: Mapped["User"] = relationship()
    requirement_statuses: Mapped[list[RegistrationRequirementStatus]] = relationship(
        back_populates="registration", cascade="all, delete-orphan"
    )


class RegistrationRequirementStatus(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "registration_requirement_statuses"
    __table_args__ = (UniqueConstraint("registration_id", "requirement_id"),)

    registration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("registrations.id", ondelete="CASCADE"), index=True
    )
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("event_requirements.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(32), default=RequirementStatus.NOT_DONE, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    review_reason: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    registration: Mapped[Registration] = relationship(back_populates="requirement_statuses")
    requirement: Mapped["EventRequirement"] = relationship()


class WaitlistEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "waitlist_entries"
    __table_args__ = (UniqueConstraint("event_id", "user_id"),)

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    registration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("registrations.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


from app.models.event import Event, EventRequirement  # noqa: E402
from app.models.user import User  # noqa: E402
