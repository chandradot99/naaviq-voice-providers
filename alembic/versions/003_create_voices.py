"""create voices table

Revision ID: 003
Revises: 002
Create Date: 2026-04-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voices",
        sa.Column("id", UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("voice_id", sa.String(256), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("gender", sa.String(16), nullable=True),
        sa.Column("category", sa.String(32), nullable=True),
        sa.Column("languages", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("preview_url", sa.String(512), nullable=True),
        sa.Column("accent", sa.String(64), nullable=True),
        sa.Column("age", sa.String(32), nullable=True),
        sa.Column("use_cases", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("tags", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("compatible_models", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("meta", JSONB, nullable=False, server_default="{}"),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.provider_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "voice_id", name="uq_voices_provider_voice"),
    )

    op.create_index("idx_voices_provider", "voices", ["provider_id"])
    op.create_index("idx_voices_active", "voices", ["deprecated_at"],
                    postgresql_where=sa.text("deprecated_at IS NULL"))


def downgrade() -> None:
    op.drop_index("idx_voices_active")
    op.drop_index("idx_voices_provider")
    op.drop_table("voices")
