"""social follow proofs, winner relay, organizer-controlled archiving

Revision ID: 0008_social_winner_archive
Revises: 0007_event_views
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0008_social_winner_archive"
down_revision = "0007_event_views"
branch_labels = None
depends_on = None


NEW_EVENT_COLUMNS = (
    ("archived_at", lambda: sa.Column("archived_at", sa.DateTime(timezone=True))),
    ("payout_contact", lambda: sa.Column("payout_contact", sa.String(length=128))),
    ("social_url", lambda: sa.Column("social_url", sa.Text())),
    ("social_platform", lambda: sa.Column("social_platform", sa.String(length=32))),
    ("social_note", lambda: sa.Column("social_note", sa.Text())),
)


def _uuid_type(bind):
    if bind.dialect.name == "sqlite":
        return sa.CHAR(36)
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    uuid_type = _uuid_type(bind)

    event_cols = {c["name"] for c in insp.get_columns("events")}
    for name, factory in NEW_EVENT_COLUMNS:
        if name not in event_cols:
            op.add_column("events", factory())
    if "archived_at" not in event_cols:
        op.create_index("ix_events_archived_at", "events", ["archived_at"])

    org_cols = {c["name"] for c in insp.get_columns("organizers")}
    if "payout_contact" not in org_cols:
        op.add_column("organizers", sa.Column("payout_contact", sa.String(length=128)))

    tables = set(insp.get_table_names())

    if "social_proofs" not in tables:
        op.create_table(
            "social_proofs",
            sa.Column("id", uuid_type, primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("event_id", uuid_type, nullable=False),
            sa.Column("user_id", uuid_type, nullable=False),
            sa.Column("file_id", sa.String(length=256), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("reviewed_by", uuid_type),
            sa.Column("reviewed_at", sa.DateTime(timezone=True)),
            sa.Column("review_note", sa.Text()),
            sa.PrimaryKeyConstraint("id", name="pk_social_proofs"),
            sa.ForeignKeyConstraint(
                ["event_id"], ["events.id"], ondelete="CASCADE", name="fk_social_proofs_event_id"
            ),
            sa.ForeignKeyConstraint(
                ["user_id"], ["users.id"], ondelete="CASCADE", name="fk_social_proofs_user_id"
            ),
            sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], name="fk_social_proofs_reviewed_by"),
            sa.UniqueConstraint("event_id", "user_id", name="uq_social_proofs_event_user"),
        )
        op.create_index("ix_social_proofs_event_id", "social_proofs", ["event_id"])
        op.create_index("ix_social_proofs_user_id", "social_proofs", ["user_id"])
        op.create_index("ix_social_proofs_status", "social_proofs", ["status"])

    if "winner_messages" not in tables:
        op.create_table(
            "winner_messages",
            sa.Column("id", uuid_type, primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("claim_id", uuid_type, nullable=False),
            sa.Column("sender_id", uuid_type),
            sa.Column("direction", sa.String(length=32), nullable=False, server_default="to_winner"),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("delivered", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.PrimaryKeyConstraint("id", name="pk_winner_messages"),
            sa.ForeignKeyConstraint(
                ["claim_id"], ["winner_claims.id"], ondelete="CASCADE", name="fk_winner_messages_claim_id"
            ),
            sa.ForeignKeyConstraint(
                ["sender_id"], ["users.id"], ondelete="SET NULL", name="fk_winner_messages_sender_id"
            ),
        )
        op.create_index("ix_winner_messages_claim_id", "winner_messages", ["claim_id"])

    # Capacity is now unlimited (0). Customs that are still live should stop
    # rejecting players the moment they hit the old hard-coded 100.
    op.execute(
        sa.text(
            "UPDATE events SET capacity = 0 "
            "WHERE status IN ('draft', 'pending_approval', 'published', 'full', 'started')"
        )
    )
    op.execute(
        sa.text("UPDATE events SET status = 'published' WHERE status = 'full'")
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "winner_messages" in tables:
        op.drop_table("winner_messages")
    if "social_proofs" in tables:
        op.drop_table("social_proofs")
    org_cols = {c["name"] for c in insp.get_columns("organizers")}
    if "payout_contact" in org_cols:
        op.drop_column("organizers", "payout_contact")
    event_cols = {c["name"] for c in insp.get_columns("events")}
    # SQLite refuses to drop a column an index still points at
    if "ix_events_archived_at" in {ix["name"] for ix in insp.get_indexes("events")}:
        op.drop_index("ix_events_archived_at", table_name="events")
    for name, _ in reversed(NEW_EVENT_COLUMNS):
        if name in event_cols:
            op.drop_column("events", name)
