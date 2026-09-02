from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.enums import SocialProofStatus


class SocialProof(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A player's screenshot proving they followed the organizer's page.

    This is the last gate before a registration is confirmed, and only exists
    for events where the organizer asked for one. The organizer reviews it; the
    bot owner can review it too when the organizer goes quiet.
    """

    __tablename__ = "social_proofs"
    __table_args__ = (UniqueConstraint("event_id", "user_id", name="uq_social_proofs_event_user"),)

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    file_id: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=SocialProofStatus.PENDING, nullable=False, index=True
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)

    event: Mapped["Event"] = relationship(foreign_keys="[SocialProof.event_id]")
    user: Mapped["User"] = relationship(foreign_keys="[SocialProof.user_id]")


from app.models.event import Event  # noqa: E402
from app.models.user import User  # noqa: E402
