from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UUIDPrimaryKeyMixin


class EventView(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One row per person who actually looked at a custom's card.

    Registrations only exist once someone taps "I joined", so without this the
    top of the funnel is invisible: an organizer cannot tell "nobody saw it"
    apart from "everybody saw it and bounced".
    """

    __tablename__ = "event_views"
    __table_args__ = (UniqueConstraint("event_id", "user_id", name="uq_event_views_event_user"),)

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    #: "deep_link" when they arrived through the organizer's link, "list" from
    #: the in-bot list, "digest" from the daily broadcast.
    source: Mapped[str | None] = mapped_column(String(32))
