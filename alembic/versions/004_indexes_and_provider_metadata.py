"""add performance indexes and provider metadata columns

Revision ID: 004
Revises: 003
Create Date: 2026-04-17

Changes:
  - Enable pg_trgm extension for fast substring search (ilike)
  - GIN indexes on voices.languages, voices.compatible_models, models.languages (array contains)
  - B-tree indexes on voices.gender, voices.accent (filter columns)
  - Trigram indexes on voices.display_name, models.display_name (search filter)
  - providers.source column — "api" | "docs" | "mixed"
  - providers.last_synced_at column — when the provider was last successfully synced
"""

from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pg_trgm enables fast ILIKE/similarity search via trigram indexes
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Provider metadata
    op.add_column("providers", sa.Column("source", sa.String(8), nullable=True))
    op.add_column("providers", sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True))

    # GIN indexes for ARRAY @> (contains) queries
    op.create_index("idx_voices_languages_gin", "voices", ["languages"],
                    postgresql_using="gin")
    op.create_index("idx_voices_compatible_models_gin", "voices", ["compatible_models"],
                    postgresql_using="gin")
    op.create_index("idx_models_languages_gin", "models", ["languages"],
                    postgresql_using="gin")

    # B-tree indexes for equality filter columns on voices
    op.create_index("idx_voices_gender", "voices", ["gender"])
    op.create_index("idx_voices_accent", "voices", ["accent"])

    # Trigram indexes for ILIKE substring search
    op.create_index("idx_voices_display_name_trgm", "voices", ["display_name"],
                    postgresql_using="gin",
                    postgresql_ops={"display_name": "gin_trgm_ops"})
    op.create_index("idx_models_display_name_trgm", "models", ["display_name"],
                    postgresql_using="gin",
                    postgresql_ops={"display_name": "gin_trgm_ops"})


def downgrade() -> None:
    op.drop_index("idx_models_display_name_trgm")
    op.drop_index("idx_voices_display_name_trgm")
    op.drop_index("idx_voices_accent")
    op.drop_index("idx_voices_gender")
    op.drop_index("idx_models_languages_gin")
    op.drop_index("idx_voices_compatible_models_gin")
    op.drop_index("idx_voices_languages_gin")
    op.drop_column("providers", "last_synced_at")
    op.drop_column("providers", "source")
