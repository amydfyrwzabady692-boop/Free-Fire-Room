"""event reviews and ratings

Revision ID: 0003_reviews
Revises: 0002_announcements
Create Date: 2026-08-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0003_reviews"
down_revision = "0002_announcements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "event_reviews" in insp.get_table_names():
        return
    uuid_type = postgresql.UUID(as_uuid=True)
    if bind.dialect.name == "sqlite":
        uuid_type = sa.CHAR(36)
    op.create_table(
        "event_reviews",
        sa.Column("id", uuid_type, primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reviewer_id", uuid_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", uuid_type, sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organizer_id", uuid_type, sa.ForeignKey("organizers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("prize_paid", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("comment", sa.Text()),
        sa.UniqueConstraint("reviewer_id", "event_id", name="uq_event_reviews_user_event"),
    )
    op.create_index("ix_event_reviews_reviewer_id", "event_reviews", ["reviewer_id"])
    op.create_index("ix_event_reviews_event_id", "event_reviews", ["event_id"])
    op.create_index("ix_event_reviews_organizer_id", "event_reviews", ["organizer_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "event_reviews" not in insp.get_table_names():
        return
    op.drop_index("ix_event_reviews_organizer_id", table_name="event_reviews")
    op.drop_index("ix_event_reviews_event_id", table_name="event_reviews")
    op.drop_index("ix_event_reviews_reviewer_id", table_name="event_reviews")
    op.drop_table("event_reviews")
