from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.enums import PrizePaidVote


class EventReview(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "event_reviews"
    __table_args__ = (UniqueConstraint("reviewer_id", "event_id", name="uq_event_reviews_user_event"),)

    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    organizer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizers.id", ondelete="CASCADE"), index=True
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    prize_paid: Mapped[str] = mapped_column(String(16), default=PrizePaidVote.UNKNOWN, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)

    reviewer: Mapped["User"] = relationship()
    event: Mapped["Event"] = relationship()
    organizer: Mapped["Organizer"] = relationship()


from app.models.event import Event  # noqa: E402
from app.models.organizer import Organizer  # noqa: E402
from app.models.user import User  # noqa: E402
