"""Add api_urls and docs_urls to providers table.

Revision ID: 004
Revises: 003
Create Date: 2026-04-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "providers",
        sa.Column("api_urls", ARRAY(sa.String()), nullable=False, server_default="{}"),
    )
    op.add_column(
        "providers",
        sa.Column("docs_urls", ARRAY(sa.String()), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("providers", "docs_urls")
    op.drop_column("providers", "api_urls")
