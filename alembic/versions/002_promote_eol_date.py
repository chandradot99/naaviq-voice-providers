"""promote eol_date from meta to first-class column

Revision ID: 002
Revises: 001
Create Date: 2026-04-17
"""

from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "models",
        sa.Column("eol_date", sa.String(10), nullable=True),
    )
    # Backfill from meta where present (Cartesia stored it there before this migration).
    op.execute(
        "UPDATE models SET eol_date = meta->>'eol_date' "
        "WHERE meta ? 'eol_date' AND meta->>'eol_date' IS NOT NULL"
    )
    op.execute("UPDATE models SET meta = meta - 'eol_date' WHERE meta ? 'eol_date'")


def downgrade() -> None:
    # Move back into meta before dropping the column.
    op.execute(
        "UPDATE models SET meta = meta || jsonb_build_object('eol_date', eol_date) "
        "WHERE eol_date IS NOT NULL"
    )
    op.drop_column("models", "eol_date")
