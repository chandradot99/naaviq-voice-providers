"""
Sync interface and data contracts for naaviq-voice-providers.

Design principles:
  - Providers are added manually from the admin UI — no sync needed for provider metadata
  - One sync() call per provider — internally fetches models and voices as needed
  - sync() NEVER writes to DB — returns SyncResult for naaviq-admin to diff and apply
  - All language codes are normalized to BCP-47 uppercase region (en-US, hi-IN)
  - Provider-specific extras go into the `meta` dict field

Source types:
  - "api"   : provider exposes a REST API we call directly
  - "docs"  : provider has no API; fetch docs and parse with an AI model
  - "mixed" : combine both (e.g., voices from API, models from docs)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import ClassVar, Literal

# Shared HTTP timeout for every syncer. Provider APIs typically respond well
# under 5s; 20s gives headroom for slow docs fetches in the AI parser path too.
HTTP_TIMEOUT = 20.0


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
    # Product lifecycle stage. Syncers emit "alpha" | "beta" | "ga" only.
    # "deprecated" is reserved — it's set by the admin apply logic's stale sweep
    # alongside deprecated_at, and the DB enforces the pairing via CHECK.
    lifecycle: Literal["alpha", "beta", "ga"] = "ga"
    description: str | None = None
    eol_date: str | None = None                    # "YYYY-MM-DD" if the provider publishes one
    # Audio capabilities. TTS = produced output; STT = accepted input. [] / None = unknown.
    sample_rates_hz: list[int] = field(default_factory=list)  # e.g., [8000, 16000, 24000, 48000]
    audio_formats: list[str] = field(default_factory=list)    # e.g., ["mp3", "wav", "pcm", "opus"]
    max_text_chars: int | None = None                         # TTS-only; omit for STT
    max_audio_seconds: int | None = None                      # STT-only; omit for TTS
    # Canonical capability flags. Only use strings from the vocab below; skip unknown features
    # or put them in `meta`. [] = no declared capabilities.
    #   STT: word_timestamps | speaker_diarization | punctuation | profanity_filter |
    #        custom_vocabulary | language_detection | translation | sentiment |
    #        pii_redaction | summarization | topic_detection
    #   TTS: emotion | voice_cloning | voice_design | ssml | phoneme_input |
    #        prosody_control | style_control | multi_speaker
    capabilities: list[str] = field(default_factory=list)
    # Deployment regions where the model is available. Canonical vocab:
    #   us | eu | asia | global
    # Conventions:
    #   []           = unknown (fallback; filter queries won't match)
    #   ["global"]   = available worldwide / no regional restriction
    #   ["us","eu"]  = only these regions
    # ?region=<x> matches rows containing <x> OR containing "global".
    regions: list[str] = field(default_factory=list)
    # Pricing — {} means "not recorded; check provider site". Populated shape:
    #   {
    #     "unit":       "character" | "minute" | "second" | "word" | "token" | "request" | "hour",
    #     "price_usd":  float,                          # cost per unit in USD
    #     "free_quota": {"amount": int, "unit": str, "period": "month" | "day" | "once"} | None,
    #     "variants":   [  # optional overrides (e.g., streaming vs batch, HD vs Turbo tier)
    #       {"applies_to": str, "unit": str, "price_usd": float},
    #     ],
    #     "as_of":      "YYYY-MM-DD",                   # date pricing was captured
    #     "source_url": str,                            # where the price was scraped from
    #     "notes":      str | None,
    #   }
    pricing: dict = field(default_factory=dict)
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
    # ["*"] = multilingual / supports all provider languages (mirrors the model-side convention).
    # [] = unknown / unmapped — filter queries won't match a specific language.
    languages: list[str] = field(default_factory=list)
    description: str | None = None
    preview_url: str | None = None
    accent: str | None = None
    age: str | None = None
    use_cases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    # ["*"] = works with all models (wildcard). [] = unknown/unmapped — won't match
    # any model-filter query. A specific list restricts to those model_ids.
    compatible_models: list[str] = field(default_factory=list)
    # Voice-level capability flags (canonical vocab). Most capabilities live on the model;
    # use this only when a voice opts in to a feature its model exposes.
    #   emotion | multilingual_native
    capabilities: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)       # provider-specific extras not covered above

    def __post_init__(self) -> None:
        # Normalize accent to lowercase so filter queries match regardless of
        # how the provider labels it ("British", "british", "BRITISH" all → "british").
        if self.accent:
            self.accent = self.accent.strip().lower() or None


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
    source: Literal["api", "docs", "mixed"]
    api_urls: list[str] = field(default_factory=list)   # REST endpoints called during sync
    docs_urls: list[str] = field(default_factory=list)  # documentation pages parsed during sync
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
                stt_models, tts_models = self._parse_models(data)
                tts_voices = self._parse_voices(data)
                return SyncResult(
                    stt_models=stt_models,
                    tts_models=tts_models,
                    tts_voices=tts_voices,
                    source=self.source,
                )
    """

    provider_id: ClassVar[str]
    source: ClassVar[Literal["api", "docs", "mixed"]]

    @abstractmethod
    async def sync(self) -> SyncResult:
        """
        Fetch all models and voices for this provider and return a SyncResult.
        Never writes to DB.
        """
        ...
