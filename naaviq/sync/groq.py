"""
Groq sync script.

Source: mixed
  - STT/TTS models: GET https://api.groq.com/openai/v1/models (auth required)
  - TTS voices    : AI-parsed from docs (no voice listing API)

Audio models returned by the models API and their classifications:
  STT: whisper-large-v3, whisper-large-v3-turbo
  TTS: canopylabs/orpheus-v1-english, canopylabs/orpheus-arabic-saudi,
       playai-tts, playai-tts-arabic

Auth: Authorization: Bearer header
Config: GROQ_API_KEY
"""

from __future__ import annotations

import asyncio

import httpx

from naaviq.config import settings
from naaviq.sync.ai_parser import parse_voices_from_docs
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncModel, SyncResult

_MODELS_URL = "https://api.groq.com/openai/v1/models"
_TTS_ENDPOINT = "https://api.groq.com/openai/v1/audio/speech"
_STT_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"

_DOCS_URLS = [
    "https://console.groq.com/docs/speech-to-text",
    "https://console.groq.com/docs/text-to-speech",
    "https://console.groq.com/docs/model/playai-tts",
]

_VOICES_GUIDANCE = """
Return every built-in TTS voice available on Groq. There are ~35 voices across 3 model families.

voice_id = the exact API voice string used in the `voice` parameter.
display_name = human-readable name.
languages = BCP-47 codes.
gender = "male" | "female" | null.
category = "premade".

PlayAI English voices (compatible_models=["playai-tts"]):
  Arista-PlayAI (female, en), Atlas-PlayAI (male, en), Basil-PlayAI (male, en),
  Briggs-PlayAI (male, en), Calum-PlayAI (male, en), Celeste-PlayAI (female, en),
  Cheyenne-PlayAI (female, en), Chip-PlayAI (male, en), Cillian-PlayAI (male, en),
  Deedee-PlayAI (female, en), Fritz-PlayAI (male, en), Gail-PlayAI (female, en),
  Indigo-PlayAI (female, en), Mamaw-PlayAI (female, en), Mason-PlayAI (male, en),
  Mikail-PlayAI (male, en), Mitch-PlayAI (male, en), Nia-PlayAI (female, en),
  Quinn-PlayAI (female, en), Thunder-PlayAI (male, en)

PlayAI Arabic voices (compatible_models=["playai-tts-arabic"]):
  aaliyah (female, ar), adnan (male, ar)

Orpheus English voices (compatible_models=["canopylabs/orpheus-v1-english"]):
  Autumn (female, en), Diana (female, en), Hannah (female, en),
  Austin (male, en), Daniel (male, en), Troy (male, en)

Orpheus Arabic voices (compatible_models=["canopylabs/orpheus-arabic-saudi"]):
  Abdullah (male, ar), Fahad (male, ar), Sultan (male, ar),
  Lulwa (female, ar), Noura (female, ar), Aisha (female, ar)
"""

# Which model IDs are audio and their type/metadata
_AUDIO_MODEL_META: dict[str, dict] = {
    "whisper-large-v3": {
        "type": "stt", "display_name": "Whisper Large v3", "is_default": True,
        "languages": ["*"], "streaming": False,
        "description": "Groq-hosted Whisper Large v3 — 99+ languages, word/segment timestamps, audio translation.",
        "meta": {"supports_timestamps": True, "supports_translation": True, "supports_diarization": False, "min_billing_seconds": 10},
    },
    "whisper-large-v3-turbo": {
        "type": "stt", "display_name": "Whisper Large v3 Turbo", "is_default": False,
        "languages": ["*"], "streaming": False,
        "description": "Groq-hosted Whisper Large v3 Turbo — 99+ languages, optimised for speed and cost.",
        "meta": {"supports_timestamps": True, "supports_translation": False, "supports_diarization": False, "min_billing_seconds": 10},
    },
    "canopylabs/orpheus-v1-english": {
        "type": "tts", "display_name": "Orpheus English", "is_default": True,
        "languages": ["en"], "streaming": True,
        "description": "Groq Orpheus English — expressive English TTS with vocal direction controls.",
        "meta": {},
    },
    "canopylabs/orpheus-arabic-saudi": {
        "type": "tts", "display_name": "Orpheus Arabic (Saudi)", "is_default": False,
        "languages": ["ar"], "streaming": True,
        "description": "Groq Orpheus Arabic (Saudi dialect) TTS.",
        "meta": {},
    },
    "playai-tts": {
        "type": "tts", "display_name": "PlayAI Dialog", "is_default": False,
        "languages": ["en"], "streaming": True,
        "description": "Groq PlayAI Dialog — conversational English TTS.",
        "meta": {},
    },
    "playai-tts-arabic": {
        "type": "tts", "display_name": "PlayAI Dialog Arabic", "is_default": False,
        "languages": ["ar"], "streaming": True,
        "description": "Groq PlayAI Dialog Arabic TTS.",
        "meta": {},
    },
}


class GroqSyncer(ProviderSyncer):
    provider_id = "groq"
    source = "mixed"

    async def sync(self) -> SyncResult:
        models_data, (tts_voices, voice_notes) = await asyncio.gather(
            self._fetch_models(),
            parse_voices_from_docs(
                seed_urls=_DOCS_URLS,
                provider_id=self.provider_id,
                guidance=_VOICES_GUIDANCE,
            ),
        )

        stt_models, tts_models = self._parse_models(models_data)

        from_cache = isinstance(voice_notes, dict) and voice_notes.get("source") == "cache"
        sync_notes = (
            f"{len(stt_models)} STT models, {len(tts_models)} TTS models, "
            f"{len(tts_voices)} voices (cache)."
            if from_cache else
            f"{len(stt_models)} STT models, {len(tts_models)} TTS models, "
            f"{len(tts_voices)} voices."
        )

        return SyncResult(
            stt_models=stt_models,
            tts_models=tts_models,
            tts_voices=tts_voices,
            source=self.source,
            api_urls=[_MODELS_URL, _TTS_ENDPOINT, _STT_ENDPOINT],
            docs_urls=_DOCS_URLS,
            notes=sync_notes,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_models(self) -> list[dict]:
        if not settings.groq_api_key:
            raise ValueError("GROQ_API_KEY is not set in .env")

        headers = {"Authorization": f"Bearer {settings.groq_api_key}"}
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(_MODELS_URL, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data.get("data") or []

    def _parse_models(self, models_data: list[dict]) -> tuple[list[SyncModel], list[SyncModel]]:
        stt: list[SyncModel] = []
        tts: list[SyncModel] = []

        seen_ids = {m["id"] for m in models_data if m.get("id")}

        for model_id, meta in _AUDIO_MODEL_META.items():
            if model_id not in seen_ids:
                continue  # model not returned by API — skip (may have been removed)
            m = SyncModel(
                model_id=model_id,
                display_name=meta["display_name"],
                type=meta["type"],
                languages=meta["languages"],
                streaming=meta["streaming"],
                is_default=meta["is_default"],
                description=meta["description"],
                meta=meta["meta"],
            )
            if meta["type"] == "stt":
                stt.append(m)
            else:
                tts.append(m)

        # Preserve ordering: default first
        stt.sort(key=lambda m: (not m.is_default, m.model_id))
        tts.sort(key=lambda m: (not m.is_default, m.model_id))
        return stt, tts


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    syncer = GroqSyncer()
    try:
        result = await syncer.sync()
    except httpx.HTTPStatusError as e:
        print(f"\nGroq API error ({e.response.status_code}): {e.response.text[:300]}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== STT Models ({len(result.stt_models)}) ===")
    for m in result.stt_models:
        print(f"  {m.model_id!r:30} {m.display_name!r:25} is_default={m.is_default}")

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        print(
            f"  {m.model_id!r:42} {m.display_name!r:22} "
            f"langs={m.languages} is_default={m.is_default}"
        )

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) ===")
    for v in result.tts_voices:
        print(
            f"  {v.voice_id!r:25} {v.display_name!r:15} "
            f"gender={v.gender or '?':7} langs={v.languages} models={v.compatible_models}"
        )

    print(f"\nSource : {result.source}")
    print(f"Notes  : {result.notes}")
    print(f"Fetched: {result.fetched_at}")


if __name__ == "__main__":
    asyncio.run(_main())
