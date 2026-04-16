from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from naaviq.config import settings
from naaviq.db import get_db
from naaviq.limiter import limiter
from naaviq.models import Model, Provider, Voice
from naaviq.schemas import PaginatedModels, PaginatedVoices, ProviderOut

router = APIRouter(prefix="/providers", tags=["providers"])


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
    db: AsyncSession = Depends(get_db),
):
    """List all active voice providers."""
    q = select(Provider).where(Provider.deprecated_at.is_(None)).order_by(Provider.display_name)
    if provider_type:
        q = q.where(Provider.type == provider_type)
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
    include_deprecated: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List models for a provider. Deprecated models excluded by default."""
    await _get_provider_or_404(provider_id, db)

    q = select(Model).where(Model.provider_id == provider_id)
    if not include_deprecated:
        q = q.where(Model.deprecated_at.is_(None))
    if model_type:
        q = q.where(Model.type == model_type)

    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    rows = await db.execute(q.order_by(Model.display_name).limit(limit).offset(offset))

    return PaginatedModels(total=total or 0, limit=limit, offset=offset, data=rows.scalars().all())


@router.get("/{provider_id}/voices", response_model=PaginatedVoices)
@limiter.limit(settings.rate_limit)
async def list_voices(
    request: Request,
    provider_id: str,
    gender: str | None = Query(None, description="Filter by gender: male, female, neutral"),
    category: str | None = Query(None, description="Filter by category: premade, cloned, generated"),
    language: str | None = Query(None, description="Filter by language code e.g. en-US, hi-IN"),
    include_deprecated: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List TTS voices for a provider. Deprecated voices excluded by default."""
    await _get_provider_or_404(provider_id, db)

    q = select(Voice).where(Voice.provider_id == provider_id)
    if not include_deprecated:
        q = q.where(Voice.deprecated_at.is_(None))
    if gender:
        q = q.where(Voice.gender == gender)
    if category:
        q = q.where(Voice.category == category)
    if language:
        q = q.where(Voice.languages.contains([language]))

    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    rows = await db.execute(q.order_by(Voice.display_name).limit(limit).offset(offset))

    return PaginatedVoices(total=total or 0, limit=limit, offset=offset, data=rows.scalars().all())
