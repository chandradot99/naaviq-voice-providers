"""initial schema — providers, models, voices

Revision ID: 001
Revises:
Create Date: 2026-04-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ── providers ─────────────────────────────────────────────────────────────
    op.create_table(
        "providers",
        sa.Column("id", UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("type", sa.String(8), nullable=False),
        sa.Column("website", sa.String(256), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("source", sa.String(8), nullable=True),
        sa.Column("api_urls", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("docs_urls", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id"),
        sa.CheckConstraint("type IN ('stt', 'tts', 'both')", name="ck_providers_type"),
        sa.CheckConstraint("source IN ('api', 'docs', 'mixed')", name="ck_providers_source"),
    )

    # ── models ────────────────────────────────────────────────────────────────
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
        sa.Column("lifecycle", sa.String(16), nullable=False, server_default="ga"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("eol_date", sa.Date, nullable=True),
        sa.Column("sample_rates_hz", ARRAY(sa.Integer), nullable=False, server_default="{}"),
        sa.Column("audio_formats", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("max_text_chars", sa.Integer, nullable=True),
        sa.Column("max_audio_seconds", sa.Integer, nullable=True),
        sa.Column("capabilities", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("regions", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("pricing", JSONB, nullable=False, server_default="{}"),
        sa.Column("meta", JSONB, nullable=False, server_default="{}"),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["provider_id"], ["providers.provider_id"],
            onupdate="CASCADE", ondelete="RESTRICT",
            name="fk_models_provider_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "model_id", "type", name="uq_models_provider_model_type"),
        sa.CheckConstraint("type IN ('stt', 'tts')", name="ck_models_type"),
        sa.CheckConstraint("lifecycle IN ('alpha', 'beta', 'ga', 'deprecated')", name="ck_models_lifecycle"),
        sa.CheckConstraint(
            "(lifecycle = 'deprecated') = (deprecated_at IS NOT NULL)",
            name="ck_models_lifecycle_deprecated_sync",
        ),
    )

    op.create_index("idx_models_provider_type", "models", ["provider_id", "type"])
    op.create_index("idx_models_active", "models", ["provider_id"],
                    postgresql_where=sa.text("deprecated_at IS NULL"))
    op.create_index("uq_models_one_default_per_provider_type", "models", ["provider_id", "type"],
                    unique=True,
                    postgresql_where=sa.text("is_default AND deprecated_at IS NULL"))
    op.create_index("idx_models_languages_gin", "models", ["languages"],
                    postgresql_using="gin")
    op.create_index("idx_models_capabilities_gin", "models", ["capabilities"],
                    postgresql_using="gin")
    op.create_index("idx_models_regions_gin", "models", ["regions"],
                    postgresql_using="gin")
    op.create_index("idx_models_display_name_trgm", "models", ["display_name"],
                    postgresql_using="gin",
                    postgresql_ops={"display_name": "gin_trgm_ops"})

    # ── voices ────────────────────────────────────────────────────────────────
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
        sa.Column("capabilities", ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("meta", JSONB, nullable=False, server_default="{}"),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["provider_id"], ["providers.provider_id"],
            onupdate="CASCADE", ondelete="RESTRICT",
            name="fk_voices_provider_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "voice_id", name="uq_voices_provider_voice"),
        sa.CheckConstraint("gender IN ('male', 'female', 'neutral')", name="ck_voices_gender"),
        sa.CheckConstraint("category IN ('premade', 'cloned', 'generated')", name="ck_voices_category"),
    )

    op.create_index("idx_voices_provider", "voices", ["provider_id"])
    op.create_index("idx_voices_active", "voices", ["provider_id"],
                    postgresql_where=sa.text("deprecated_at IS NULL"))
    op.create_index("idx_voices_gender", "voices", ["gender"])
    op.create_index("idx_voices_accent", "voices", ["accent"])
    op.create_index("idx_voices_languages_gin", "voices", ["languages"],
                    postgresql_using="gin")
    op.create_index("idx_voices_compatible_models_gin", "voices", ["compatible_models"],
                    postgresql_using="gin")
    op.create_index("idx_voices_capabilities_gin", "voices", ["capabilities"],
                    postgresql_using="gin")
    op.create_index("idx_voices_display_name_trgm", "voices", ["display_name"],
                    postgresql_using="gin",
                    postgresql_ops={"display_name": "gin_trgm_ops"})

    # ── sync_runs ─────────────────────────────────────────────────────────────
    op.create_table(
        "sync_runs",
        sa.Column("id", UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.String(8), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stats", JSONB, nullable=False, server_default="{}"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["provider_id"], ["providers.provider_id"],
            onupdate="CASCADE", ondelete="RESTRICT",
            name="fk_sync_runs_provider_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('success', 'error')", name="ck_sync_runs_status"),
        sa.CheckConstraint("source IS NULL OR source IN ('api', 'docs', 'mixed')", name="ck_sync_runs_source"),
        sa.CheckConstraint(
            "(status = 'error') = (error IS NOT NULL)",
            name="ck_sync_runs_error_matches_status",
        ),
    )

    op.create_index("idx_sync_runs_provider_finished", "sync_runs",
                    ["provider_id", sa.text("finished_at DESC")])
    op.create_index("idx_sync_runs_finished", "sync_runs",
                    [sa.text("finished_at DESC")])


def downgrade() -> None:
    op.drop_table("sync_runs")
    op.drop_table("voices")
    op.drop_table("models")
    op.drop_table("providers")
