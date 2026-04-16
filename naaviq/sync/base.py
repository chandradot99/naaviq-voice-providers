"""
Sync interface and data contracts for naaviq-voice-providers.

Design principles:
  - Providers are added manually from the admin UI — no sync needed for provider metadata
  - One sync() call per provider — internally fetches models and voices as needed
  - sync() NEVER writes to DB — returns SyncResult for naaviq-admin to diff and apply
  - All language codes are normalized to BCP-47 uppercase region (en-US, hi-IN)
  - Provider-specific extras go into the `meta` dict field

Two source types:
  - "api"  : provider exposes a REST API we call directly
  - "docs" : provider has no API; fetch docs and parse with an AI model (Claude/GPT/Gemini)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import ClassVar, Literal


# ── Core data types ───────────────────────────────────────────────────────────

@dataclass
class SyncModel:
    """
    A single STT or TTS model.

    `meta` holds provider-specific fields that don't belong in the core schema.
    Examples:
      Deepgram STT : {"architecture": "base", "batch": True, "formatted_output": True}
      ElevenLabs   : {"max_characters": 10000, "latency_optimization": True}
    """
    model_id: str                                  # "nova-3", "sonic-3", "saaras:v3"
    display_name: str
    type: Literal["stt", "tts"]
    languages: list[str] = field(default_factory=list)  # normalized BCP-47: ["*"] or ["en-US", "hi-IN"]
    streaming: bool = True
    is_default: bool = False
    description: str | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class SyncVoice:
    """
    A single TTS voice.

    `meta` holds provider-specific fields.
    Examples:
      Deepgram TTS  : {"accent": "American", "age": "Adult", "use_cases": ["IVR"], "tags": ["deep"]}
      ElevenLabs    : {"accent": "british", "age": "young", "labels": {"use_case": "narration"}}
      Cartesia      : {"emotion_support": True, "volume_support": True}
      OpenAI        : {"instructions_support": True, "model_exclusive": "gpt-4o-mini-tts"}
    """
    voice_id: str                                  # "alloy", "aura-2-zeus-en", "meera"
    display_name: str
    gender: Literal["male", "female", "neutral"] | None = None
    category: Literal["premade", "cloned", "generated"] | None = None
    languages: list[str] = field(default_factory=list)  # [] means multilingual / no restriction
    description: str | None = None
    preview_url: str | None = None
    accent: str | None = None
    age: str | None = None
    use_cases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)       # provider-specific extras not covered above


# ── Sync result ───────────────────────────────────────────────────────────────

@dataclass
class SyncResult:
    """
    The unified output of a provider sync() call.

    naaviq-admin receives this and computes a diff against the current DB state
    independently for stt_models, tts_models, and tts_voices before applying.
    Sync scripts never write to DB.
    """
    stt_models: list[SyncModel]
    tts_models: list[SyncModel]
    tts_voices: list[SyncVoice]
    source: Literal["api", "docs"]
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    notes: str | None = None


# ── Base syncer ───────────────────────────────────────────────────────────────

class ProviderSyncer(ABC):
    """
    Base class for all provider sync scripts.

    Implement sync() — one call per provider. Internally use private helpers
    (_fetch_models, _fetch_voices, etc.) as needed. The admin UI has one
    "Sync" button per provider; naaviq-admin diffs models and voices separately
    from the returned SyncResult.

    Always use normalize_languages() from naaviq.sync.language to normalize
    language codes before returning them in SyncModel or SyncVoice.

    Example:
        class DeepgramSyncer(ProviderSyncer):
            provider_id = "deepgram"
            source = "api"

            async def sync(self) -> SyncResult:
                data = await self._fetch_raw()
                models = self._parse_models(data)
                voices = self._parse_voices(data)
                return SyncResult(models=models, voices=voices, source=self.source)
    """

    provider_id: ClassVar[str]
    source: ClassVar[Literal["api", "docs"]]

    @abstractmethod
    async def sync(self) -> SyncResult:
        """
        Fetch all models and voices for this provider and return a SyncResult.
        Never writes to DB.
        """
        ...
