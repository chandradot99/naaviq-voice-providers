"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-04-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("type", sa.String(8), nullable=False),
        sa.Column("website", sa.String(256), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id"),
    )

    op.create_table(
        "models",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("type", sa.String(8), nullable=False),
        sa.Column("languages", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("streaming", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.provider_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "model_id", "type", name="uq_models_provider_model_type"),
    )

    op.create_table(
        "voices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("voice_id", sa.String(256), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("gender", sa.String(16), nullable=True),
        sa.Column("category", sa.String(32), nullable=True),
        sa.Column("languages", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("preview_url", sa.String(512), nullable=True),
        sa.Column("accent", sa.String(64), nullable=True),
        sa.Column("age", sa.String(32), nullable=True),
        sa.Column("use_cases", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("tags", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.provider_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "voice_id", name="uq_voices_provider_voice"),
    )

    # Indexes
    op.create_index("idx_models_provider_type", "models", ["provider_id", "type"])
    op.create_index("idx_models_active", "models", ["deprecated_at"], postgresql_where=sa.text("deprecated_at IS NULL"))
    op.create_index("idx_voices_provider", "voices", ["provider_id"])
    op.create_index("idx_voices_active", "voices", ["deprecated_at"], postgresql_where=sa.text("deprecated_at IS NULL"))


def downgrade() -> None:
    op.drop_index("idx_voices_active")
    op.drop_index("idx_voices_provider")
    op.drop_index("idx_models_active")
    op.drop_index("idx_models_provider_type")
    op.drop_table("voices")
    op.drop_table("models")
    op.drop_table("providers")
