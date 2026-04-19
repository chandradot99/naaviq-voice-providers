"""
Speechify sync script.

Source: mixed
  - TTS voices: GET https://api.speechify.ai/v1/voices (no pagination, auth required)
  - TTS models: AI-parsed from docs (Simba model family)
  - STT: not offered — stt_models=[]

Voice object shape:
  {
    "id": "henry",
    "display_name": "Henry",
    "gender": "male",          -- "male" | "female" | "notSpecified"
    "locale": "en-US",
    "type": "shared",          -- "shared" | "personal" — only "shared" voices included
    "models": [{"model": "simba-english", "supported_locales": ["en-US"]}],
    "avatar_image": "...",
    "preview_audio": "...",
    "tags": ["narration"]
  }

Auth: Authorization: Bearer header
Config: SPEECHIFY_API_KEY
"""

from __future__ import annotations

import asyncio

import httpx

from naaviq.config import settings
from naaviq.sync.ai_parser import parse_models_from_docs
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncResult, SyncVoice
from naaviq.sync.language import normalize_languages

_BASE_URL = "https://api.speechify.ai/v1"
_VOICES_URL = f"{_BASE_URL}/voices"

_DOCS_URLS = [
    "https://docs.speechify.com/docs/text-to-speech-api-overview",
    "https://docs.speechify.com/reference/create-speech",
    "https://docs.speechify.com/docs/voices",
]

_TTS_MODEL_GUIDANCE = """
Extract Speechify TTS models. There are 2 active models in the Simba family.
Do NOT include deprecated models simba-base or simba-turbo.

1. model_id="simba-english", display_name="Simba English", is_default=True, streaming=True
   - Best quality model, English only.
   - description="Speechify Simba English — highest quality English TTS."
   - languages: ["en"]

2. model_id="simba-multilingual", display_name="Simba Multilingual", is_default=False, streaming=True
   - Experimental multilingual model, 50+ languages.
   - description="Speechify Simba Multilingual — 50+ language experimental TTS."
   - languages: ["*"]
   - meta: {"status": "experimental"}

Use exact model_id values as listed above.
"""

_GENDER_MAP = {"male": "male", "female": "female", "notspecified": "neutral"}


class SpeechifySyncer(ProviderSyncer):
    provider_id = "speechify"
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
        if not settings.speechify_api_key:
            raise ValueError("SPEECHIFY_API_KEY is not set in .env")

        headers = {"Authorization": f"Bearer {settings.speechify_api_key}"}
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(_VOICES_URL, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            voices = data if isinstance(data, list) else (data.get("voices") or data.get("data") or [])
            return [v for v in voices if v.get("type") == "shared"]

    def _parse_voices(self, voices_data: list[dict], tts_models: list) -> list[SyncVoice]:
        all_model_ids = [m.model_id for m in tts_models]
        voices: list[SyncVoice] = []

        for v in voices_data:
            voice_id = v.get("id")
            if not voice_id:
                continue

            gender = _GENDER_MAP.get((v.get("gender") or "").lower())

            locale = v.get("locale")
            if locale:
                languages = normalize_languages([locale])
            else:
                languages = ["en"]

            voice_models = v.get("models") or []
            compatible = [m["model"] for m in voice_models if m.get("model") in all_model_ids]
            if not compatible:
                compatible = []  # works with all models

            preview_url = v.get("preview_audio") or None

            voices.append(SyncVoice(
                voice_id=voice_id,
                display_name=v.get("display_name") or voice_id,
                gender=gender,
                category="premade",
                languages=languages,
                age=None,
                description=None,
                preview_url=preview_url,
                compatible_models=compatible,
                meta={
                    "tags": v.get("tags") or [],
                    "avatar_image": v.get("avatar_image") or None,
                },
            ))
        return voices


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    syncer = SpeechifySyncer()
    try:
        result = await syncer.sync()
    except httpx.HTTPStatusError as e:
        print(f"\nSpeechify API error ({e.response.status_code}): {e.response.text[:300]}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        print(
            f"  {m.model_id!r:25} {m.display_name!r:22} "
            f"langs={m.languages} is_default={m.is_default}"
        )

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) ===")
    for v in result.tts_voices[:20]:
        print(
            f"  {v.voice_id!r:20} {v.display_name!r:20} "
            f"gender={v.gender or '?':8} langs={v.languages}"
        )
    if len(result.tts_voices) > 20:
        print(f"  ... and {len(result.tts_voices) - 20} more")

    print(f"\nSource : {result.source}")
    print(f"Notes  : {result.notes}")
    print(f"Fetched: {result.fetched_at}")


if __name__ == "__main__":
    asyncio.run(_main())
