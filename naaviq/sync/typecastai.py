"""
Typecast AI sync script.

Source: mixed
  - TTS voices: GET https://api.typecast.ai/v2/voices (no pagination, auth required)
  - TTS models: AI-parsed from docs (SSFM model family)
  - STT: not offered — stt_models=[]

Voice object shape:
  {
    "voice_id": "tc_abc123",
    "voice_name": "Emily",
    "gender": "female",       -- "male" | "female"
    "age": "young_adult",     -- "child" | "teen" | "young_adult" | "adult" | "mature"
    "models": ["ssfm-v30", "ssfm-v21"],
    "use_cases": ["narration", "social_media"],
    "language": "eng"         -- ISO 639-3 code
  }

Auth: X-API-KEY header
Config: TYPECAST_API_KEY
"""

from __future__ import annotations

import asyncio

import httpx

from naaviq.config import settings
from naaviq.sync.ai_parser import parse_models_from_docs
from naaviq.sync.base import ProviderSyncer, SyncResult, SyncVoice

_BASE_URL = "https://api.typecast.ai"
_VOICES_URL = f"{_BASE_URL}/v2/voices"

_DOCS_URLS = [
    "https://docs.typecast.ai/",
    "https://typecast.ai/docs/api-reference/text-to-speech/text-to-speech",
    "https://typecast.ai/docs/quickstart",
]

_TTS_MODEL_GUIDANCE = """
Extract Typecast AI TTS models. There are 2 models in the SSFM family.

1. model_id="ssfm-v30", display_name="SSFM v3.0", is_default=True, streaming=False
   - Latest model (January 2026). Enhanced prosody, pacing, emotional expression.
   - Supports 37 languages.
   - description="Typecast SSFM v3.0 — enhanced prosody and emotional expression, 37 languages."
   - languages: ["*"]

2. model_id="ssfm-v21", display_name="SSFM v2.1", is_default=False, streaming=False
   - Stable production model, works with 680+ voice characters.
   - description="Typecast SSFM v2.1 — stable production model, 680+ voices."
   - languages: ["*"]

Use exact model_id values: "ssfm-v30" and "ssfm-v21".
"""

# ISO 639-3 → BCP-47
_ISO3_TO_BCP47: dict[str, str] = {
    "eng": "en",
    "kor": "ko",
    "spa": "es",
    "jpn": "ja",
    "zho": "zh",
    "cmn": "zh",
    "fra": "fr",
    "deu": "de",
    "ger": "de",
    "por": "pt",
    "hin": "hi",
    "ara": "ar",
    "vie": "vi",
    "tha": "th",
    "ind": "id",
    "msa": "ms",
    "tur": "tr",
    "pol": "pl",
    "rus": "ru",
    "ita": "it",
    "nld": "nl",
    "swe": "sv",
    "nor": "no",
    "dan": "da",
    "fin": "fi",
    "ces": "cs",
    "slk": "sk",
    "hun": "hu",
    "ron": "ro",
    "ukr": "uk",
    "bul": "bg",
    "hrv": "hr",
    "ell": "el",
    "heb": "he",
    "tam": "ta",
    "tel": "te",
    "ben": "bn",
    "cat": "ca",
}

_GENDER_MAP = {"male": "male", "female": "female"}


class TypecastAISyncer(ProviderSyncer):
    provider_id = "typecastai"
    source = "mixed"

    async def sync(self) -> SyncResult:
        voices_data, (tts_models, tts_notes) = await asyncio.gather(
            self._fetch_voices(),
            parse_models_from_docs(
                seed_urls=_DOCS_URLS,
                provider_id=self.provider_id,
                model_type="tts",
                guidance=_TTS_MODEL_GUIDANCE,
            ),
        )
        tts_voices = self._parse_voices(voices_data, tts_models)

        from_cache = isinstance(tts_notes, dict) and tts_notes.get("source") == "cache"
        sync_notes = (
            f"{len(tts_voices)} voices. {len(tts_models)} TTS models (cache)."
            if from_cache else
            f"{len(tts_voices)} voices. {len(tts_models)} TTS models."
        )

        return SyncResult(
            stt_models=[],
            tts_models=tts_models,
            tts_voices=tts_voices,
            source=self.source,
            api_urls=[_VOICES_URL],
            docs_urls=_DOCS_URLS,
            notes=sync_notes,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_voices(self) -> list[dict]:
        if not settings.typecast_api_key:
            raise ValueError("TYPECAST_API_KEY is not set in .env")

        headers = {"X-API-KEY": settings.typecast_api_key}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(_VOICES_URL, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else (data.get("voices") or data.get("data") or [])

    def _parse_voices(self, voices_data: list[dict], tts_models: list) -> list[SyncVoice]:
        all_model_ids = {m.model_id for m in tts_models}
        voices: list[SyncVoice] = []

        for v in voices_data:
            voice_id = v.get("voice_id")
            if not voice_id:
                continue

            gender = _GENDER_MAP.get((v.get("gender") or "").lower())

            languages = ["*"]  # API does not return per-voice language; all voices are multilingual

            raw_models = v.get("models") or []
            model_ids = [
                m.get("version", m) if isinstance(m, dict) else m
                for m in raw_models
            ]
            compatible = [m for m in model_ids if m in all_model_ids]

            voices.append(SyncVoice(
                voice_id=voice_id,
                display_name=v.get("voice_name") or voice_id,
                gender=gender,
                category="premade",
                languages=languages,
                age=v.get("age") or None,
                description=None,
                preview_url=None,
                compatible_models=compatible,
                meta={
                    "use_cases": v.get("use_cases") or [],
                    "emotions": {
                        m.get("version"): m.get("emotions", [])
                        for m in raw_models if isinstance(m, dict) and m.get("version")
                    },
                },
            ))
        return voices


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    syncer = TypecastAISyncer()
    try:
        result = await syncer.sync()
    except httpx.HTTPStatusError as e:
        print(f"\nTypecast AI API error ({e.response.status_code}): {e.response.text[:300]}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        print(
            f"  {m.model_id!r:15} {m.display_name!r:18} "
            f"langs={m.languages} is_default={m.is_default}"
        )

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) ===")
    for v in result.tts_voices[:20]:
        print(
            f"  {v.voice_id!r:20} {v.display_name!r:20} "
            f"gender={v.gender or '?':7} age={v.age or '?':12} langs={v.languages}"
        )
    if len(result.tts_voices) > 20:
        print(f"  ... and {len(result.tts_voices) - 20} more")

    print(f"\nSource : {result.source}")
    print(f"Notes  : {result.notes}")
    print(f"Fetched: {result.fetched_at}")


if __name__ == "__main__":
    asyncio.run(_main())
