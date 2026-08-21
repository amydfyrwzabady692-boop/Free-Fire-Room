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
    uuid_type = postgresql.UUID(as_uuid=True)
    if bind.dialect.name == "sqlite":
        uuid_type = sa.CHAR(36)
    if "winner_claims" not in set(insp.get_table_names()):
        op.create_table(
            "winner_claims",
            sa.Column("id", uuid_type, primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("event_id", uuid_type, nullable=False),
            sa.Column("user_id", uuid_type, nullable=False),
            sa.Column("organizer_id", uuid_type),
            sa.Column("screenshot_file_id", sa.String(length=256), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("admin_note", sa.Text()),
            sa.Column("reviewed_by", uuid_type),
            sa.Column("reviewed_at", sa.DateTime(timezone=True)),
            sa.PrimaryKeyConstraint("id", name="pk_winner_claims"),
            sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE", name="fk_winner_claims_event_id"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE", name="fk_winner_claims_user_id"),
            sa.ForeignKeyConstraint(
                ["organizer_id"], ["organizers.id"], ondelete="SET NULL", name="fk_winner_claims_organizer_id"
            ),
            sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], name="fk_winner_claims_reviewed_by"),
            sa.UniqueConstraint("event_id", "user_id", name="uq_winner_claims_event_user"),
        )
        insp = inspect(bind)
    existing = {ix["name"] for ix in insp.get_indexes("winner_claims")}
    for name, cols in (
        ("ix_winner_claims_event_id", ["event_id"]),
        ("ix_winner_claims_user_id", ["user_id"]),
        ("ix_winner_claims_organizer_id", ["organizer_id"]),
        ("ix_winner_claims_status", ["status"]),
    ):
        if name not in existing:
            op.create_index(name, "winner_claims", cols)


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "winner_claims" not in set(insp.get_table_names()):
        return
    existing = {ix["name"] for ix in insp.get_indexes("winner_claims")}
    for name in (
        "ix_winner_claims_status",
        "ix_winner_claims_organizer_id",
        "ix_winner_claims_user_id",
        "ix_winner_claims_event_id",
    ):
        if name in existing:
            op.drop_index(name, table_name="winner_claims")
    op.drop_table("winner_claims")
