"""Pydantic response schemas for the public API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class ProviderOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    provider_id: str
    display_name: str
    type: str
    website: str | None
    description: str | None
    deprecated_at: datetime | None
    updated_at: datetime


class ModelOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    provider_id: str
    model_id: str
    display_name: str
    type: str
    languages: list[str]
    streaming: bool
    is_default: bool
    description: str | None
    eol_date: str | None
    meta: dict
    deprecated_at: datetime | None
    updated_at: datetime


class VoiceOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    provider_id: str
    voice_id: str
    display_name: str
    gender: str | None
    category: str | None
    languages: list[str]
    description: str | None
    preview_url: str | None
    accent: str | None
    age: str | None
    use_cases: list[str]
    tags: list[str]
    meta: dict
    deprecated_at: datetime | None
    updated_at: datetime


class PaginatedModels(BaseModel):
    total: int
    limit: int
    offset: int
    data: list[ModelOut]


class PaginatedVoices(BaseModel):
    total: int
    limit: int
    offset: int
    data: list[VoiceOut]
