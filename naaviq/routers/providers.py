from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from naaviq.config import settings
from naaviq.db import get_db
from naaviq.limiter import limiter
from naaviq.models import Model, Provider, Voice
from naaviq.schemas import PaginatedModels, PaginatedVoices, ProviderOut

router = APIRouter(prefix="/providers", tags=["providers"])


_STT_MODEL_CAPABILITIES = {
    "word_timestamps", "speaker_diarization", "punctuation", "profanity_filter",
    "custom_vocabulary", "language_detection", "translation", "sentiment",
    "pii_redaction", "summarization", "topic_detection",
}
_TTS_MODEL_CAPABILITIES = {
    "emotion", "voice_cloning", "voice_design", "ssml", "phoneme_input",
    "prosody_control", "style_control", "multi_speaker",
}
_MODEL_CAPABILITIES = _STT_MODEL_CAPABILITIES | _TTS_MODEL_CAPABILITIES
_VOICE_CAPABILITIES = {"emotion", "multilingual_native"}

_VALID_LIFECYCLES = {"alpha", "beta", "ga", "deprecated"}
_VALID_REGIONS = {"us", "eu", "asia", "global"}


def _parse_capabilities(raw: str | None, valid: set[str], kind: str) -> list[str]:
    """Comma-separated capabilities param → list, validated against the canonical vocab."""
    if not raw:
        return []
    parts = [c.strip() for c in raw.split(",") if c.strip()]
    bad = [p for p in parts if p not in valid]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {kind} capability: {bad}. Allowed: {sorted(valid)}",
        )
    return parts

_UPDATED_SINCE_DESC = (
    "ISO-8601 timestamp; returns rows where updated_at >= this value. "
    "When set, deprecated rows are included by default (so deprecation events "
    "are visible to polling consumers); pass include_deprecated=false to override."
)


def _effective_include_deprecated(
    include_deprecated: bool | None, updated_since: datetime | None
) -> bool:
    """Auto-flip include_deprecated to True when polling with updated_since."""
    if include_deprecated is not None:
        return include_deprecated
    return updated_since is not None


def _validate_region(raw: str | None) -> str | None:
    """Single region param → lowercase string. Rejects unknown values with 400."""
    if not raw:
        return None
    val = raw.strip().lower()
    if val not in _VALID_REGIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid region: {val!r}. Allowed: {sorted(_VALID_REGIONS)}",
        )
    return val


def _parse_lifecycles(raw: str | None) -> list[str]:
    """Comma-separated lifecycle param → list. Rejects unknown values with 400."""
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    bad = [p for p in parts if p not in _VALID_LIFECYCLES]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid lifecycle value(s): {bad}. Allowed: {sorted(_VALID_LIFECYCLES)}",
        )
    return parts


async def _get_provider_or_404(provider_id: str, db: AsyncSession) -> Provider:
    result = await db.execute(
        select(Provider)
        .where(Provider.provider_id == provider_id)
        .where(Provider.deprecated_at.is_(None))
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    return provider


@router.get("", response_model=list[ProviderOut])
@limiter.limit(settings.rate_limit)
async def list_providers(
    request: Request,
    provider_type: str | None = Query(None, alias="type", description="Filter by type: stt, tts, both"),
    updated_since: datetime | None = Query(None, description=_UPDATED_SINCE_DESC),
    include_deprecated: bool | None = Query(None, description="Include deprecated providers. Default: false, or true when updated_since is set."),
    db: AsyncSession = Depends(get_db),
):
    """List voice providers."""
    q = select(Provider).order_by(Provider.display_name, Provider.provider_id)
    if not _effective_include_deprecated(include_deprecated, updated_since):
        q = q.where(Provider.deprecated_at.is_(None))
    if provider_type:
        q = q.where(Provider.type == provider_type)
    if updated_since:
        q = q.where(Provider.updated_at >= updated_since)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{provider_id}", response_model=ProviderOut)
@limiter.limit(settings.rate_limit)
async def get_provider(
    request: Request,
    provider_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a single active provider by ID."""
    return await _get_provider_or_404(provider_id, db)


@router.get("/{provider_id}/models", response_model=PaginatedModels)
@limiter.limit(settings.rate_limit)
async def list_models(
    request: Request,
    provider_id: str,
    model_type: str | None = Query(None, alias="type", description="Filter by type: stt, tts"),
    capabilities: str | None = Query(None, description="Comma-separated canonical capabilities; returns models supporting ALL of them (e.g., 'word_timestamps,speaker_diarization')"),
    lifecycle: str | None = Query(None, description="Comma-separated lifecycle stages: alpha, beta, ga, deprecated (e.g., 'ga' or 'alpha,beta')"),
    region: str | None = Query(None, description="Deployment region: us, eu, asia, global. Matches models that list this region OR 'global' (worldwide)."),
    updated_since: datetime | None = Query(None, description=_UPDATED_SINCE_DESC),
    include_deprecated: bool | None = Query(None, description="Include deprecated models. Default: false, or true when updated_since is set."),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List models for a provider. Deprecated models excluded by default."""
    await _get_provider_or_404(provider_id, db)

    q = select(Model).where(Model.provider_id == provider_id)
    if not _effective_include_deprecated(include_deprecated, updated_since):
        q = q.where(Model.deprecated_at.is_(None))
    if model_type:
        q = q.where(Model.type == model_type)
    caps = _parse_capabilities(capabilities, _MODEL_CAPABILITIES, "model")
    if caps:
        q = q.where(Model.capabilities.contains(caps))
    stages = _parse_lifecycles(lifecycle)
    if stages:
        q = q.where(Model.lifecycle.in_(stages))
    region_val = _validate_region(region)
    if region_val:
        q = q.where(or_(
            Model.regions.contains([region_val]),
            Model.regions.contains(["global"]),
        ))
    if updated_since:
        q = q.where(Model.updated_at >= updated_since)

    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    rows = await db.execute(q.order_by(Model.display_name, Model.model_id).limit(limit).offset(offset))

    return PaginatedModels(total=total or 0, limit=limit, offset=offset, data=rows.scalars().all())


@router.get("/{provider_id}/voices", response_model=PaginatedVoices)
@limiter.limit(settings.rate_limit)
async def list_voices(
    request: Request,
    provider_id: str,
    gender: str | None = Query(None, description="Filter by gender: male, female, neutral"),
    category: str | None = Query(None, description="Filter by category: premade, cloned, generated"),
    language: str | None = Query(None, description="Filter by language code e.g. en-US, hi-IN"),
    model: str | None = Query(None, description="Filter by compatible TTS model ID e.g. aura-2, Chirp3-HD. Returns voices where compatible_models contains this value OR contains '*' (works with all models)."),
    accent: str | None = Query(None, description="Filter by accent e.g. british, american, indian"),
    capabilities: str | None = Query(None, description="Comma-separated canonical capabilities; returns voices declaring ALL of them (e.g., 'emotion')"),
    search: str | None = Query(None, description="Search voice display name (case-insensitive substring match)"),
    updated_since: datetime | None = Query(None, description=_UPDATED_SINCE_DESC),
    include_deprecated: bool | None = Query(None, description="Include deprecated voices. Default: false, or true when updated_since is set."),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List TTS voices for a provider. Deprecated voices excluded by default."""
    await _get_provider_or_404(provider_id, db)

    q = select(Voice).where(Voice.provider_id == provider_id)
    if not _effective_include_deprecated(include_deprecated, updated_since):
        q = q.where(Voice.deprecated_at.is_(None))
    if gender:
        q = q.where(Voice.gender == gender)
    if category:
        q = q.where(Voice.category == category)
    if language:
        q = q.where(or_(
            Voice.languages.contains([language]),
            Voice.languages.contains(["*"]),
        ))
    if model:
        q = q.where(or_(
            Voice.compatible_models.contains([model]),
            Voice.compatible_models.contains(["*"]),
        ))
    if accent:
        q = q.where(func.lower(Voice.accent) == accent.strip().lower())
    caps = _parse_capabilities(capabilities, _VOICE_CAPABILITIES, "voice")
    if caps:
        q = q.where(Voice.capabilities.contains(caps))
    if search:
        q = q.where(Voice.display_name.ilike(f"%{search}%"))
    if updated_since:
        q = q.where(Voice.updated_at >= updated_since)

    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    rows = await db.execute(q.order_by(Voice.display_name, Voice.voice_id).limit(limit).offset(offset))

    return PaginatedVoices(total=total or 0, limit=limit, offset=offset, data=rows.scalars().all())
