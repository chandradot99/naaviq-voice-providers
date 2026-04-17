"""create models table

Revision ID: 002
Revises: 001
Create Date: 2026-04-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "models",
        sa.Column("id", UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("type", sa.String(8), nullable=False),
        sa.Column("languages", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("streaming", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("eol_date", sa.String(10), nullable=True),
        sa.Column("meta", JSONB, nullable=False, server_default="{}"),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.provider_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "model_id", "type", name="uq_models_provider_model_type"),
    )

    op.create_index("idx_models_provider_type", "models", ["provider_id", "type"])
    op.create_index("idx_models_active", "models", ["deprecated_at"],
                    postgresql_where=sa.text("deprecated_at IS NULL"))


def downgrade() -> None:
    op.drop_index("idx_models_active")
    op.drop_index("idx_models_provider_type")
    op.drop_table("models")
