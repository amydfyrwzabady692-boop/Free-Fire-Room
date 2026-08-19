"""audit_logs.actor_telegram_id to bigint

Revision ID: 0005_audit_telegram_bigint
Revises: 0004_event_poster
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0005_audit_telegram_bigint"
down_revision = "0004_event_poster"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "audit_logs" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("audit_logs")}
    if "actor_telegram_id" not in cols:
        return
    if bind.dialect.name != "postgresql":
        return
    op.alter_column(
        "audit_logs",
        "actor_telegram_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "audit_logs" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("audit_logs")}
    if "actor_telegram_id" not in cols:
        return
    if bind.dialect.name != "postgresql":
        return
    op.alter_column(
        "audit_logs",
        "actor_telegram_id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
