"""several follow pages per custom, each with its own screenshot

Revision ID: 0009_social_tasks
Revises: 0008_social_winner_archive
Create Date: 2026-09-05
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0009_social_tasks"
down_revision = "0008_social_winner_archive"
branch_labels = None
depends_on = None


def _uuid_type(bind):
    if bind.dialect.name == "sqlite":
        return sa.CHAR(36)
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    uuid_type = _uuid_type(bind)
    sqlite = bind.dialect.name == "sqlite"
    tables = set(insp.get_table_names())

    if "event_social_tasks" not in tables:
        op.create_table(
            "event_social_tasks",
            sa.Column("id", uuid_type, primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("event_id", uuid_type, nullable=False),
            sa.Column("url", sa.Text(), nullable=False),
            sa.Column("platform", sa.String(length=32), nullable=False, server_default="other"),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.PrimaryKeyConstraint("id", name="pk_event_social_tasks"),
            sa.ForeignKeyConstraint(
                ["event_id"], ["events.id"], ondelete="CASCADE", name="fk_event_social_tasks_event_id"
            ),
        )
        op.create_index("ix_event_social_tasks_event_id", "event_social_tasks", ["event_id"])

    proof_cols = {c["name"] for c in insp.get_columns("social_proofs")}
    if "task_id" not in proof_cols:
        # SQLite cannot add a column with an inline FK to an existing table, and
        # the constraint is only a safety net here, so add it plain there.
        if sqlite:
            op.add_column("social_proofs", sa.Column("task_id", uuid_type))
        else:
            op.add_column("social_proofs", sa.Column("task_id", uuid_type))
            op.create_foreign_key(
                "fk_social_proofs_task_id",
                "social_proofs",
                "event_social_tasks",
                ["task_id"],
                ["id"],
                ondelete="CASCADE",
            )
        op.create_index("ix_social_proofs_task_id", "social_proofs", ["task_id"])

    # every custom that already asked for a follow becomes one task, and the
    # screenshots already collected are attached to it
    op.execute(
        sa.text(
            "INSERT INTO event_social_tasks "
            "(id, created_at, updated_at, event_id, url, platform, sort_order, is_active) "
            "SELECT " + _new_uuid(sqlite) + ", " + _now(sqlite) + ", " + _now(sqlite) + ", "
            "e.id, e.social_url, COALESCE(e.social_platform, 'other'), 0, "
            + ("1" if sqlite else "true") + " "
            "FROM events e "
            "WHERE e.social_url IS NOT NULL AND e.social_url <> '' "
            "AND NOT EXISTS (SELECT 1 FROM event_social_tasks t WHERE t.event_id = e.id)"
        )
    )
    op.execute(
        sa.text(
            "UPDATE social_proofs SET task_id = ("
            "SELECT t.id FROM event_social_tasks t "
            "WHERE t.event_id = social_proofs.event_id ORDER BY t.sort_order LIMIT 1"
            ") WHERE task_id IS NULL"
        )
    )

    # one screenshot per (player, page) instead of per (player, custom)
    if not sqlite:
        existing = {c["name"] for c in insp.get_unique_constraints("social_proofs")}
        if "uq_social_proofs_event_user" in existing:
            op.drop_constraint("uq_social_proofs_event_user", "social_proofs", type_="unique")
        if "uq_social_proofs_event_user_task" not in existing:
            op.create_unique_constraint(
                "uq_social_proofs_event_user_task",
                "social_proofs",
                ["event_id", "user_id", "task_id"],
            )


def _new_uuid(sqlite: bool) -> str:
    if sqlite:
        # good enough for a backfill: SQLite is only used by the test suite
        return (
            "lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || "
            "substr(lower(hex(randomblob(2))),2) || '-a' || substr(lower(hex(randomblob(2))),2) "
            "|| '-' || lower(hex(randomblob(6)))"
        )
    return "gen_random_uuid()"


def _now(sqlite: bool) -> str:
    return "datetime('now')" if sqlite else "now()"


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    sqlite = bind.dialect.name == "sqlite"

    if not sqlite:
        existing = {c["name"] for c in insp.get_unique_constraints("social_proofs")}
        if "uq_social_proofs_event_user_task" in existing:
            op.drop_constraint("uq_social_proofs_event_user_task", "social_proofs", type_="unique")

    insp = inspect(bind)
    proof_cols = {c["name"] for c in insp.get_columns("social_proofs")}
    if "task_id" in proof_cols:
        for ix in insp.get_indexes("social_proofs"):
            if "task_id" in (ix.get("column_names") or []):
                op.drop_index(ix["name"], table_name="social_proofs")
        if sqlite:
            # SQLite has to rebuild the table to drop a column, and Alembic
            # rebuilds it from the ORM metadata - which still declares
            # task_id and the unique constraint over it, so the drop can
            # never succeed. The column is nullable and unused once the
            # table is gone; leaving it costs nothing on the only database
            # that hits this branch, the local test harness.
            pass
        else:
            try:
                op.drop_constraint(
                    "fk_social_proofs_task_id", "social_proofs", type_="foreignkey"
                )
            except Exception:  # noqa: BLE001 - already gone
                pass
            op.drop_column("social_proofs", "task_id")

    if "event_social_tasks" in set(insp.get_table_names()):
        op.drop_table("event_social_tasks")
