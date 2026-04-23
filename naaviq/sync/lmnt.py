"""
LMNT sync script.

Source: mixed
  - TTS voices: GET https://api.lmnt.com/v1/ai/voice/list?owner=system (API)
  - TTS models: AI-parsed from docs (no /models endpoint)
  - STT: not offered — stt_models=[]

Voice API response shape (array):
  [
    {
      "id": "amy",
      "name": "Amy",
      "owner": "system",
      "state": "ready",
      "description": "Narrative. Excited. US",
      "gender": "F",            -- single-char: F, M, U (not full words)
      "type": "professional",   -- "instant" or "professional"
      "starred": false,
      "tags": ["primary:support agent", "healthcare agent"],
      "image_url": null,
      "preview_url": "https://api.lmnt.com/v1/ai/voice/amy/preview"
    },
    ...
  ]

TTS models (no API endpoint — AI-parsed from docs):
  - blizzard        : flagship model, default, streaming
  - lmnt-tts-0216  : latency-optimized variant, streaming

Auth: X-API-Key header, LMNT_API_KEY env var.
"""

from __future__ import annotations

import asyncio

import httpx

from naaviq.config import settings
from naaviq.sync.ai_parser import parse_models_from_docs
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncModel, SyncResult, SyncVoice
from naaviq.sync.language import normalize_languages

_VOICES_URL = "https://api.lmnt.com/v1/ai/voice/list"

_DOCS_URLS = [
    "https://docs.lmnt.com/api-reference/speech/synthesize-speech-bytes",
    "https://docs.lmnt.com/guides/models",
]

_MODEL_GUIDANCE = """
Extract LMNT TTS models. There are 2 models.

1. model_id="blizzard", display_name="LMNT Blizzard", is_default=True, streaming=True
   - LMNT's flagship TTS model. Extract the supported languages list from docs.
   - description="LMNT's flagship TTS model — natural, expressive, high-quality."

2. model_id="lmnt-tts-0216", display_name="LMNT TTS 0216", is_default=False, streaming=True
   - Latency-optimized variant. Same language support as Blizzard.
   - description="Latency-optimized LMNT TTS variant for real-time use cases."

Use exact model_id values as listed above.
"""

# Gender normalization — API returns single-char codes: F, M, U
_GENDER_MAP: dict[str, str] = {
    "f": "female",
    "m": "male",
    "u": "neutral",
    # also handle full strings in case the API changes
    "female":    "female",
    "male":      "male",
    "nonbinary": "neutral",
}


class LmntSyncer(ProviderSyncer):
    provider_id = "lmnt"
    source = "mixed"

    async def sync(self) -> SyncResult:
        voices_data, (tts_models, notes) = await asyncio.gather(
            self._fetch_voices(),
            parse_models_from_docs(
                seed_urls=_DOCS_URLS,
                provider_id=self.provider_id,
                model_type="tts",
                guidance=_MODEL_GUIDANCE,
            ),
        )
        all_langs = sorted({lang for m in tts_models for lang in m.languages})
        tts_voices = self._parse_voices(voices_data, all_langs)

        from_cache = isinstance(notes, dict) and notes.get("source") == "cache"
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
        if not settings.lmnt_api_key:
            raise ValueError("LMNT_API_KEY is not set in .env")

        headers = {"X-API-Key": settings.lmnt_api_key}
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(
                _VOICES_URL,
                headers=headers,
                params={"owner": "system"},
            )
            resp.raise_for_status()
            return resp.json()

    def _parse_voices(self, voices_data: list[dict], all_langs: list[str]) -> list[SyncVoice]:
        voices: list[SyncVoice] = []
        for v in voices_data:
            voice_id = v.get("id")
            if not voice_id:
                continue

            # Only include system voices that are ready
            if v.get("owner") != "system" or v.get("state") != "ready":
                continue

            gender_raw = (v.get("gender") or "").lower()
            gender = _GENDER_MAP.get(gender_raw)

            raw_tags: list[str] = v.get("tags") or []
            use_cases = [t.split(":", 1)[-1] for t in raw_tags]  # strip "primary:" prefix if present

            voices.append(SyncVoice(
                voice_id=voice_id,
                display_name=v.get("name", voice_id),
                gender=gender,
                category="premade",
                languages=all_langs,
                description=v.get("description") or None,
                preview_url=v.get("preview_url") or None,
                use_cases=use_cases,
                compatible_models=["*"],  # all voices work with all LMNT models
                meta={"type": v.get("type")},
            ))
        return voices


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    syncer = LmntSyncer()
    try:
        result = await syncer.sync()
    except ValueError as e:
        print(f"\nError: {e}", file=sys.stderr)
        raise SystemExit(1)
    except httpx.HTTPStatusError as e:
        print(f"\nLMNT API error ({e.response.status_code}): {e.response.text[:300]}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        print(
            f"  {m.model_id!r:20} {m.display_name!r:25} "
            f"langs={m.languages} is_default={m.is_default}"
        )

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) ===")
    for v in result.tts_voices:
        print(
            f"  {v.voice_id!r:20} {v.display_name!r:25} "
            f"gender={v.gender or '?':6} preview={'yes' if v.preview_url else 'no'}"
        )

    print(f"\nSource: {result.source}")
    print(f"Fetched at: {result.fetched_at}")


if __name__ == "__main__":
    asyncio.run(_main())
