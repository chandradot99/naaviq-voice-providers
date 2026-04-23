"""SQLAlchemy ORM models for the Naaviq voice provider registry."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, func, select, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from naaviq.db import Base


class Provider(Base):
    __tablename__ = "providers"
    __table_args__ = (
        CheckConstraint("type IN ('stt', 'tts', 'both')", name="ck_providers_type"),
        CheckConstraint("source IN ('api', 'docs', 'mixed')", name="ck_providers_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(8), nullable=False)        # "stt" | "tts" | "both"
    website: Mapped[str | None] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(8))               # "api" | "docs" | "mixed"
    api_urls: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    docs_urls: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deprecated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Model(Base):
    __tablename__ = "models"
    __table_args__ = (
        CheckConstraint("type IN ('stt', 'tts')", name="ck_models_type"),
        CheckConstraint("lifecycle IN ('alpha', 'beta', 'ga', 'deprecated')", name="ck_models_lifecycle"),
        CheckConstraint(
            "(lifecycle = 'deprecated') = (deprecated_at IS NOT NULL)",
            name="ck_models_lifecycle_deprecated_sync",
        ),
        Index(
            "uq_models_one_default_per_provider_type",
            "provider_id", "type",
            unique=True,
            postgresql_where=text("is_default AND deprecated_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("providers.provider_id", onupdate="CASCADE", ondelete="RESTRICT", name="fk_models_provider_id"),
        nullable=False,
    )
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(8), nullable=False)        # "stt" | "tts"
    languages: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    streaming: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lifecycle: Mapped[str] = mapped_column(String(16), nullable=False, default="ga")  # alpha | beta | ga | deprecated
    description: Mapped[str | None] = mapped_column(Text)
    eol_date: Mapped[date | None] = mapped_column(Date)       # if provider publishes it
    # Audio capabilities — TTS=output, STT=input. See sync/base.py for conventions.
    sample_rates_hz: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False, default=list)
    audio_formats: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    max_text_chars: Mapped[int | None] = mapped_column(Integer)        # TTS only
    max_audio_seconds: Mapped[int | None] = mapped_column(Integer)     # STT only
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)  # canonical vocab in sync/base.py
    regions: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)        # canonical vocab in sync/base.py
    pricing: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # see sync/base.py for shape
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # provider-specific extras
    deprecated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SyncRun(Base):
    """
    Audit record for a single sync attempt against one provider.

    A row here means "we attempted a write-path sync for provider X" — it records
    success and failure alike. Dry-runs do NOT produce rows (they don't touch
    DB state). Admin `/fetch`-only errors don't produce rows either; only the
    `/apply` stage (and the scripts/sync.py --apply path) writes here.
    """
    __tablename__ = "sync_runs"
    __table_args__ = (
        CheckConstraint("status IN ('success', 'error')", name="ck_sync_runs_status"),
        CheckConstraint(
            "source IS NULL OR source IN ('api', 'docs', 'mixed')",
            name="ck_sync_runs_source",
        ),
        CheckConstraint(
            "(status = 'error') = (error IS NOT NULL)",
            name="ck_sync_runs_error_matches_status",
        ),
        Index("idx_sync_runs_provider_finished", "provider_id", text("finished_at DESC")),
        Index("idx_sync_runs_finished", text("finished_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("providers.provider_id", onupdate="CASCADE", ondelete="RESTRICT", name="fk_sync_runs_provider_id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)       # "success" | "error"
    source: Mapped[str | None] = mapped_column(String(8))                  # null on fetch-stage errors
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # {"stt": {"added": N, "updated": N, "deprecated": N}, "tts": {...}, "voices": {...}}
    stats: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


async def derive_provider_type(provider_id: str, session: AsyncSession) -> str | None:
    """
    Compute a provider's effective type from its active (non-deprecated) models.

    Returns "stt" | "tts" | "both" when at least one active model exists, or None
    when the provider has no active models (caller should keep the seeded value).

    Call after applying a SyncResult to realign Provider.type with reality — the
    seeded value from the registry can drift when a provider adds or drops
    capabilities (e.g., a TTS-only provider releases an STT model).
    """
    result = await session.execute(
        select(Model.type)
        .where(Model.provider_id == provider_id)
        .where(Model.deprecated_at.is_(None))
        .distinct()
    )
    types = {row[0] for row in result.all()}
    has_stt = "stt" in types
    has_tts = "tts" in types
    if has_stt and has_tts:
        return "both"
    if has_stt:
        return "stt"
    if has_tts:
        return "tts"
    return None


class Voice(Base):
    __tablename__ = "voices"
    __table_args__ = (
        CheckConstraint("gender IN ('male', 'female', 'neutral')", name="ck_voices_gender"),
        CheckConstraint("category IN ('premade', 'cloned', 'generated')", name="ck_voices_category"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("providers.provider_id", onupdate="CASCADE", ondelete="RESTRICT", name="fk_voices_provider_id"),
        nullable=False,
    )
    voice_id: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    gender: Mapped[str | None] = mapped_column(String(16))              # "male" | "female" | "neutral"
    category: Mapped[str | None] = mapped_column(String(32))            # "premade" | "cloned" | "generated"
    languages: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    description: Mapped[str | None] = mapped_column(Text)
    preview_url: Mapped[str | None] = mapped_column(String(512))
    accent: Mapped[str | None] = mapped_column(String(64))
    age: Mapped[str | None] = mapped_column(String(32))
    use_cases: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    compatible_models: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)  # canonical vocab in sync/base.py
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # provider-specific extras
    deprecated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
