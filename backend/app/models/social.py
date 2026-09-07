from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.enums import SocialPlatform, SocialProofStatus


class EventSocialTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One page the organizer wants followed before a player can register.

    A custom can ask for several - two Instagram pages and a YouTube channel,
    say - and each gets its own screenshot and its own approval, so the
    organizer can see exactly which page a player skipped.
    """

    __tablename__ = "event_social_tasks"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(String(32), default=SocialPlatform.OTHER, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    event: Mapped["Event"] = relationship(foreign_keys="[EventSocialTask.event_id]")


class SocialProof(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A player's screenshot proving they followed the organizer's page.

    Sending it is the requirement - the registration is final the moment the row
    exists, and nobody has to approve it. ``status`` is an audit trail rather
    than a gate: the organizer (or the bot owner) can look through the archive
    later and REJECT a screenshot that shows something else, which voids that
    player's entry until they send a real one.
    """

    __tablename__ = "social_proofs"
    __table_args__ = (
        UniqueConstraint("event_id", "user_id", "task_id", name="uq_social_proofs_event_user_task"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    #: which page this screenshot is for; NULL only on rows written before
    #: a custom could ask for more than one page
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("event_social_tasks.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=SocialProofStatus.PENDING, nullable=False, index=True
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)

    event: Mapped["Event"] = relationship(foreign_keys="[SocialProof.event_id]")
    task: Mapped["EventSocialTask | None"] = relationship(foreign_keys="[SocialProof.task_id]")
    user: Mapped["User"] = relationship(foreign_keys="[SocialProof.user_id]")


from app.models.event import Event  # noqa: E402
from app.models.user import User  # noqa: E402
