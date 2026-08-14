"""add custom announcements

Revision ID: 0002_announcements
Revises: 0001_initial
Create Date: 2026-08-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0002_announcements"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "custom_announcements" in insp.get_table_names():
        return
    uuid_type = postgresql.UUID(as_uuid=True)
    json_type = postgresql.JSONB(astext_type=sa.Text())
    if bind.dialect.name == "sqlite":
        uuid_type = sa.CHAR(36)
        json_type = sa.Text()
    op.create_table(
        "custom_announcements",
        sa.Column("id", uuid_type, primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", uuid_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("channel_name", sa.String(128), nullable=False),
        sa.Column("channel_username", sa.String(64)),
        sa.Column("channel_url", sa.Text()),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Tehran"),
        sa.Column("prize_summary", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("extra_join_links", json_type),
        sa.Column("region", sa.String(32), nullable=False, server_default="ME"),
        sa.Column("game_mode", sa.String(16), nullable=False, server_default="squad"),
        sa.Column("status", sa.String(32), nullable=False, server_default="published"),
        sa.Column("hidden_reason", sa.Text()),
        sa.Column("hidden_by", uuid_type, sa.ForeignKey("users.id")),
    )
    op.create_index("ix_custom_announcements_user_id", "custom_announcements", ["user_id"])
    op.create_index("ix_custom_announcements_starts_at", "custom_announcements", ["starts_at"])
    op.create_index("ix_custom_announcements_status", "custom_announcements", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "custom_announcements" not in insp.get_table_names():
        return
    op.drop_index("ix_custom_announcements_status", table_name="custom_announcements")
    op.drop_index("ix_custom_announcements_starts_at", table_name="custom_announcements")
    op.drop_index("ix_custom_announcements_user_id", table_name="custom_announcements")
    op.drop_table("custom_announcements")
