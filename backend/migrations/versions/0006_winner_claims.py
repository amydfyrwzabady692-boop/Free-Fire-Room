"""winner claims

Revision ID: 0006_winner_claims
Revises: 0005_audit_telegram_bigint
Create Date: 2026-08-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0006_winner_claims"
down_revision = "0005_audit_telegram_bigint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "winner_claims" in insp.get_table_names():
        return
    uuid_type = postgresql.UUID(as_uuid=True)
    if bind.dialect.name == "sqlite":
        uuid_type = sa.CHAR(36)
    op.create_table(
        "winner_claims",
        sa.Column("id", uuid_type, primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("event_id", uuid_type, sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", uuid_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organizer_id", uuid_type, sa.ForeignKey("organizers.id", ondelete="SET NULL")),
        sa.Column("screenshot_file_id", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("admin_note", sa.Text()),
        sa.Column("reviewed_by", uuid_type, sa.ForeignKey("users.id")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("event_id", "user_id", name="uq_winner_claims_event_user"),
    )
    op.create_index("ix_winner_claims_event_id", "winner_claims", ["event_id"])
    op.create_index("ix_winner_claims_user_id", "winner_claims", ["user_id"])
    op.create_index("ix_winner_claims_organizer_id", "winner_claims", ["organizer_id"])
    op.create_index("ix_winner_claims_status", "winner_claims", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "winner_claims" not in insp.get_table_names():
        return
    op.drop_index("ix_winner_claims_status", table_name="winner_claims")
    op.drop_index("ix_winner_claims_organizer_id", table_name="winner_claims")
    op.drop_index("ix_winner_claims_user_id", table_name="winner_claims")
    op.drop_index("ix_winner_claims_event_id", table_name="winner_claims")
    op.drop_table("winner_claims")
