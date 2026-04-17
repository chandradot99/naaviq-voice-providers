"""add compatible_models column to voices

Revision ID: 003
Revises: 002
Create Date: 2026-04-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "voices",
        sa.Column("compatible_models", ARRAY(sa.String), nullable=False, server_default="{}"),
    )
    # Backfill from provider-specific meta keys
    op.execute(
        "UPDATE voices SET compatible_models = ARRAY[meta->>'architecture'] "
        "WHERE provider_id = 'deepgram' AND meta->>'architecture' IS NOT NULL"
    )
    op.execute(
        "UPDATE voices SET compatible_models = ARRAY[meta->>'tier'] "
        "WHERE provider_id = 'google-cloud' AND meta->>'tier' IS NOT NULL"
    )
    op.execute(
        "UPDATE voices SET compatible_models = ARRAY[meta->>'compatible_model'] "
        "WHERE provider_id = 'sarvam' AND meta->>'compatible_model' IS NOT NULL"
    )
    # ElevenLabs: high_quality_base_model_ids is a JSON array — convert to PG array
    op.execute(
        "UPDATE voices SET compatible_models = ARRAY("
        "  SELECT jsonb_array_elements_text(meta->'high_quality_base_model_ids')"
        ") WHERE provider_id = 'elevenlabs' "
        "AND meta ? 'high_quality_base_model_ids' "
        "AND jsonb_array_length(meta->'high_quality_base_model_ids') > 0"
    )
    # OpenAI: model_exclusive → [model] if present
    op.execute(
        "UPDATE voices SET compatible_models = ARRAY[meta->>'model_exclusive'] "
        "WHERE provider_id = 'openai' AND meta->>'model_exclusive' IS NOT NULL"
    )
    # Cartesia: [] (all-to-all) — already the default

    # Strip migrated keys from meta
    op.execute("UPDATE voices SET meta = meta - 'architecture' WHERE provider_id = 'deepgram' AND meta ? 'architecture'")
    op.execute("UPDATE voices SET meta = meta - 'tier' WHERE provider_id = 'google-cloud' AND meta ? 'tier'")
    op.execute("UPDATE voices SET meta = meta - 'compatible_model' WHERE provider_id = 'sarvam' AND meta ? 'compatible_model'")
    op.execute("UPDATE voices SET meta = meta - 'high_quality_base_model_ids' WHERE provider_id = 'elevenlabs' AND meta ? 'high_quality_base_model_ids'")
    op.execute("UPDATE voices SET meta = meta - 'model_exclusive' WHERE provider_id = 'openai' AND meta ? 'model_exclusive'")


def downgrade() -> None:
    # Move back into meta before dropping
    op.execute(
        "UPDATE voices SET meta = meta || jsonb_build_object('architecture', compatible_models[1]) "
        "WHERE provider_id = 'deepgram' AND array_length(compatible_models, 1) > 0"
    )
    op.execute(
        "UPDATE voices SET meta = meta || jsonb_build_object('tier', compatible_models[1]) "
        "WHERE provider_id = 'google-cloud' AND array_length(compatible_models, 1) > 0"
    )
    op.execute(
        "UPDATE voices SET meta = meta || jsonb_build_object('compatible_model', compatible_models[1]) "
        "WHERE provider_id = 'sarvam' AND array_length(compatible_models, 1) > 0"
    )
    op.execute(
        "UPDATE voices SET meta = meta || jsonb_build_object('high_quality_base_model_ids', to_jsonb(compatible_models)) "
        "WHERE provider_id = 'elevenlabs' AND array_length(compatible_models, 1) > 0"
    )
    op.execute(
        "UPDATE voices SET meta = meta || jsonb_build_object('model_exclusive', compatible_models[1]) "
        "WHERE provider_id = 'openai' AND array_length(compatible_models, 1) > 0"
    )
    op.drop_column("voices", "compatible_models")
