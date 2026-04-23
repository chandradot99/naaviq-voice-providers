"""Cross-provider catalog endpoints — search models and voices across all providers."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from naaviq.config import settings
from naaviq.db import get_db
from naaviq.limiter import limiter
from naaviq.models import Model, Provider, Voice
from naaviq.routers.providers import (
    _MODEL_CAPABILITIES,
    _UPDATED_SINCE_DESC,
    _VOICE_CAPABILITIES,
    _effective_include_deprecated,
    _parse_capabilities,
    _parse_lifecycles,
    _validate_region,
)
from naaviq.schemas import PaginatedModels, PaginatedVoices

router = APIRouter(tags=["catalog"])

_ACTIVE_PROVIDERS = select(Provider.provider_id).where(Provider.deprecated_at.is_(None))


@router.get("/models", response_model=PaginatedModels)
@limiter.limit(settings.rate_limit)
async def list_all_models(
    request: Request,
    provider: str | None = Query(None, description="Filter by provider ID e.g. deepgram, elevenlabs"),
    model_type: str | None = Query(None, alias="type", description="Filter by type: stt, tts"),
    language: str | None = Query(None, description="Filter by language code e.g. en-US, hi-IN"),
    capabilities: str | None = Query(None, description="Comma-separated canonical capabilities; returns models supporting ALL of them (e.g., 'word_timestamps,speaker_diarization')"),
    lifecycle: str | None = Query(None, description="Comma-separated lifecycle stages: alpha, beta, ga, deprecated (e.g., 'ga' or 'alpha,beta')"),
    region: str | None = Query(None, description="Deployment region: us, eu, asia, global. Matches models that list this region OR 'global'."),
    search: str | None = Query(None, description="Search model display name (case-insensitive)"),
    updated_since: datetime | None = Query(None, description=_UPDATED_SINCE_DESC),
    include_deprecated: bool | None = Query(None, description="Include deprecated models. Default: false, or true when updated_since is set."),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List models across all active providers."""
    q = select(Model).where(Model.provider_id.in_(_ACTIVE_PROVIDERS))
    if not _effective_include_deprecated(include_deprecated, updated_since):
        q = q.where(Model.deprecated_at.is_(None))
    if provider:
        q = q.where(Model.provider_id == provider)
    if model_type:
        q = q.where(Model.type == model_type)
    if language:
        q = q.where(Model.languages.contains([language]))
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
    if search:
        q = q.where(Model.display_name.ilike(f"%{search}%"))
    if updated_since:
        q = q.where(Model.updated_at >= updated_since)

    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    rows = await db.execute(q.order_by(Model.provider_id, Model.display_name, Model.model_id).limit(limit).offset(offset))

    return PaginatedModels(total=total or 0, limit=limit, offset=offset, data=rows.scalars().all())


@router.get("/voices", response_model=PaginatedVoices)
@limiter.limit(settings.rate_limit)
async def list_all_voices(
    request: Request,
    provider: str | None = Query(None, description="Filter by provider ID e.g. deepgram, elevenlabs"),
    gender: str | None = Query(None, description="Filter by gender: male, female, neutral"),
    category: str | None = Query(None, description="Filter by category: premade, cloned, generated"),
    language: str | None = Query(None, description="Filter by language code e.g. en-US, hi-IN"),
    model: str | None = Query(None, description="Filter by compatible TTS model ID. Returns voices that explicitly list this model OR contain '*' (works with all)."),
    accent: str | None = Query(None, description="Filter by accent e.g. british, american, indian"),
    capabilities: str | None = Query(None, description="Comma-separated canonical capabilities; returns voices declaring ALL of them (e.g., 'emotion')"),
    search: str | None = Query(None, description="Search voice display name (case-insensitive)"),
    updated_since: datetime | None = Query(None, description=_UPDATED_SINCE_DESC),
    include_deprecated: bool | None = Query(None, description="Include deprecated voices. Default: false, or true when updated_since is set."),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List voices across all active providers."""
    q = select(Voice).where(Voice.provider_id.in_(_ACTIVE_PROVIDERS))
    if not _effective_include_deprecated(include_deprecated, updated_since):
        q = q.where(Voice.deprecated_at.is_(None))
    if provider:
        q = q.where(Voice.provider_id == provider)
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
    rows = await db.execute(q.order_by(Voice.provider_id, Voice.display_name, Voice.voice_id).limit(limit).offset(offset))

    return PaginatedVoices(total=total or 0, limit=limit, offset=offset, data=rows.scalars().all())
