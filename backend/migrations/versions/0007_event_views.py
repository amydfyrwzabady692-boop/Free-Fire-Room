"""event views for the organizer funnel

Revision ID: 0007_event_views
Revises: 0006_winner_claims
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0007_event_views"
down_revision = "0006_winner_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    uuid_type = postgresql.UUID(as_uuid=True)
    if bind.dialect.name == "sqlite":
        uuid_type = sa.CHAR(36)
    if "event_views" not in set(insp.get_table_names()):
        op.create_table(
            "event_views",
            sa.Column("id", uuid_type, primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("event_id", uuid_type, nullable=False),
            sa.Column("user_id", uuid_type, nullable=False),
            sa.Column("source", sa.String(length=32)),
            sa.PrimaryKeyConstraint("id", name="pk_event_views"),
            sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE", name="fk_event_views_event_id"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="fk_event_views_user_id"),
            sa.UniqueConstraint("event_id", "user_id", name="uq_event_views_event_user"),
        )
        insp = inspect(bind)
    existing = {ix["name"] for ix in insp.get_indexes("event_views")}
    for name, cols in (
        ("ix_event_views_event_id", ["event_id"]),
        ("ix_event_views_user_id", ["user_id"]),
    ):
        if name not in existing:
            op.create_index(name, "event_views", cols)


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "event_views" not in set(insp.get_table_names()):
        return
    existing = {ix["name"] for ix in insp.get_indexes("event_views")}
    for name in ("ix_event_views_user_id", "ix_event_views_event_id"):
        if name in existing:
            op.drop_index(name, table_name="event_views")
    op.drop_table("event_views")
