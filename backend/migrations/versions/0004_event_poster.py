"""event prize_summary and banner_file_id

Revision ID: 0004_event_poster
Revises: 0003_reviews
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0004_event_poster"
down_revision = "0003_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "events" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("events")}
    if "prize_summary" not in cols:
        op.add_column("events", sa.Column("prize_summary", sa.Text()))
    if "banner_file_id" not in cols:
        op.add_column("events", sa.Column("banner_file_id", sa.String(length=256)))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "events" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("events")}
    if "prize_summary" in cols:
        op.drop_column("events", "prize_summary")
    if "banner_file_id" in cols:
        op.drop_column("events", "banner_file_id")
