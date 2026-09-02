from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.enums import WinnerClaimStatus, WinnerMessageDirection


class WinnerClaim(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "winner_claims"
    __table_args__ = (UniqueConstraint("event_id", "user_id", name="uq_winner_claims_event_user"),)

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    organizer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizers.id", ondelete="SET NULL"), index=True
    )
    screenshot_file_id: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=WinnerClaimStatus.PENDING, nullable=False, index=True
    )
    admin_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    event: Mapped["Event"] = relationship(foreign_keys="[WinnerClaim.event_id]")
    user: Mapped["User"] = relationship(foreign_keys="[WinnerClaim.user_id]")


class WinnerMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One leg of the organizer <-> winner conversation, relayed by the bot.

    Neither side needs the other's Telegram account open: the bot carries the
    text both ways and keeps a record of what was said about a prize.
    """

    __tablename__ = "winner_messages"

    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("winner_claims.id", ondelete="CASCADE"), index=True
    )
    sender_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    direction: Mapped[str] = mapped_column(
        String(32), default=WinnerMessageDirection.TO_WINNER, nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    claim: Mapped[WinnerClaim] = relationship(foreign_keys="[WinnerMessage.claim_id]")


from app.models.event import Event  # noqa: E402
from app.models.user import User  # noqa: E402
