"""
Smallest AI sync script.

Source: mixed
  - TTS voices: GET https://api.smallest.ai/waves/v1/{model}/get_voices (auth required)
  - TTS models: AI-parsed from docs
  - STT models: AI-parsed from docs

Voice endpoint is per-model. Voices are fetched for the default model (lightning-v3.1).

Voice object shape:
  {
    "voiceId": "magnus",
    "displayName": "Magnus",
    "tags": {
      "language": ["en"],
      "accent": "american",
      "gender": "male"
    }
  }

Auth: Authorization: Bearer API_KEY
Config: SMALLEST_API_KEY
"""

from __future__ import annotations

import asyncio

import httpx

from naaviq.config import settings
from naaviq.sync.ai_parser import parse_models_from_docs
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncResult, SyncVoice
from naaviq.sync.language import normalize_languages

_BASE_URL = "https://api.smallest.ai/waves/v1"
_VOICES_URL = f"{_BASE_URL}/lightning-v3.1/get_voices"

_DOCS_URLS = [
    "https://waves-docs.smallest.ai/v4.0.0/content/getting-started/models",
    "https://waves-docs.smallest.ai/v4.0.0/content/text-to-speech/quickstart",
    "https://waves-docs.smallest.ai/v4.0.0/content/speech-to-text/pre-recorded/quickstart",
]

_TTS_MODEL_GUIDANCE = """
Extract Smallest AI TTS models. There are 2 models.

1. model_id="lightning-v3.1", display_name="Lightning v3.1", is_default=True, streaming=True
   - Current flagship TTS model. Sub-100ms TTFA. 15 languages.
   - Languages: English, Spanish, French, Italian, Dutch, Swedish, Portuguese, German, Hindi, Tamil, Kannada, Telugu, Malayalam, Marathi, Gujarati.
   - description="Smallest AI Lightning v3.1 — sub-100ms TTFA, 15 languages, broadcast-quality 44.1kHz audio."
   - languages: ["en", "es", "fr", "it", "nl", "sv", "pt", "de", "hi", "ta", "kn", "te", "ml", "mr", "gu"]

2. model_id="lightning-v2", display_name="Lightning v2", is_default=False, streaming=True
   - Previous generation TTS model. Still supported.
   - description="Smallest AI Lightning v2 — previous generation TTS model."
   - languages: ["en", "hi", "es", "ta"]

Use exact model_id values: "lightning-v3.1" and "lightning-v2".
"""

_STT_MODEL_GUIDANCE = """
Extract Smallest AI STT models. There is 1 model.

1. model_id="pulse", display_name="Pulse", is_default=True, streaming=True
   - Batch and real-time STT. Sub-70ms TTFT. 36 languages with auto-detection.
   - Features: word timestamps, speaker diarization, emotion detection, profanity filtering.
   - description="Smallest AI Pulse — real-time STT, 36 languages, sub-70ms latency, diarization."
   - languages: ["*"]

Use exact model_id: "pulse".
"""

_GENDER_MAP = {"male": "male", "female": "female", "neutral": "neutral"}

_LANG_NAME_TO_BCP47: dict[str, str] = {
    "english":    "en",
    "hindi":      "hi",
    "spanish":    "es",
    "french":     "fr",
    "german":     "de",
    "portuguese": "pt",
    "italian":    "it",
    "dutch":      "nl",
    "swedish":    "sv",
    "tamil":      "ta",
    "telugu":     "te",
    "kannada":    "kn",
    "malayalam":  "ml",
    "marathi":    "mr",
    "gujarati":   "gu",
    "arabic":     "ar",
    "chinese":    "zh",
    "japanese":   "ja",
    "korean":     "ko",
    "russian":    "ru",
}


class SmallestAISyncer(ProviderSyncer):
    provider_id = "smallestai"
    source = "mixed"

    async def sync(self) -> SyncResult:
        voices_data, (tts_models, tts_notes), (stt_models, stt_notes) = await asyncio.gather(
            self._fetch_voices(),
            parse_models_from_docs(
                seed_urls=_DOCS_URLS,
                provider_id=self.provider_id,
                model_type="tts",
                guidance=_TTS_MODEL_GUIDANCE,
            ),
            parse_models_from_docs(
                seed_urls=_DOCS_URLS,
                provider_id=self.provider_id,
                model_type="stt",
                guidance=_STT_MODEL_GUIDANCE,
            ),
        )
        tts_voices = self._parse_voices(voices_data)

        from_cache = (
            isinstance(tts_notes, dict) and tts_notes.get("source") == "cache"
            and isinstance(stt_notes, dict) and stt_notes.get("source") == "cache"
        )
        sync_notes = (
            f"{len(tts_voices)} voices. {len(tts_models)} TTS models, "
            f"{len(stt_models)} STT models (cache)."
            if from_cache else
            f"{len(tts_voices)} voices. {len(tts_models)} TTS models, "
            f"{len(stt_models)} STT models."
        )

        return SyncResult(
            stt_models=stt_models,
            tts_models=tts_models,
            tts_voices=tts_voices,
            source=self.source,
            api_urls=[_VOICES_URL],
            docs_urls=_DOCS_URLS,
            notes=sync_notes,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_voices(self) -> list[dict]:
        if not settings.smallest_api_key:
            raise ValueError("SMALLEST_API_KEY is not set in .env")

        headers = {"Authorization": f"Bearer {settings.smallest_api_key}"}
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(_VOICES_URL, headers=headers)
            resp.raise_for_status()
            return resp.json().get("voices") or []

    def _parse_voices(self, voices_data: list[dict]) -> list[SyncVoice]:
        voices: list[SyncVoice] = []
        for v in voices_data:
            voice_id = v.get("voiceId")
            if not voice_id:
                continue

            tags = v.get("tags") or {}
            raw_langs = tags.get("language") or []
            mapped = [_LANG_NAME_TO_BCP47.get(lang.lower(), lang) for lang in raw_langs]
            languages = normalize_languages(mapped) if mapped else ["en"]
            gender = _GENDER_MAP.get((tags.get("gender") or "").lower())
            accent = tags.get("accent") or None

            voices.append(SyncVoice(
                voice_id=voice_id,
                display_name=v.get("displayName") or voice_id,
                gender=gender,
                category="premade",
                languages=languages,
                accent=accent,
                compatible_models=["*"],  # all voices work with all Lightning models
                meta={},
            ))
        return voices


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    syncer = SmallestAISyncer()
    try:
        result = await syncer.sync()
    except httpx.HTTPStatusError as e:
        print(f"\nSmallest AI error ({e.response.status_code}): {e.response.text[:300]}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== STT Models ({len(result.stt_models)}) ===")
    for m in result.stt_models:
        print(f"  {m.model_id!r:12} {m.display_name!r:20} langs={m.languages}")

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        print(
            f"  {m.model_id!r:18} {m.display_name!r:20} "
            f"langs={m.languages} is_default={m.is_default}"
        )

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) ===")
    for v in result.tts_voices:
        print(
            f"  {v.voice_id!r:20} {v.display_name!r:22} "
            f"gender={v.gender or '?':6} accent={v.accent or '?':12} langs={v.languages}"
        )

    print(f"\nSource : {result.source}")
    print(f"Notes  : {result.notes}")
    print(f"Fetched: {result.fetched_at}")


if __name__ == "__main__":
    asyncio.run(_main())
