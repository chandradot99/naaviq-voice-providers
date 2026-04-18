"""
LMNT sync script.

Source: mixed
  - TTS voices: GET https://api.lmnt.com/v1/ai/voice/list?owner=system (API)
  - TTS models: synthetic — no /models endpoint; models are well-known constants
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

TTS models (no API endpoint — derived synthetically):
  - blizzard        : flagship model, default, streaming, 21 languages
  - lmnt-tts-0216  : latency-optimized variant, streaming, 21 languages

Auth: X-API-Key header, LMNT_API_KEY env var.
"""

from __future__ import annotations

import asyncio

import httpx

from naaviq.config import settings
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncModel, SyncResult, SyncVoice
from naaviq.sync.language import normalize_languages

_VOICES_URL = "https://api.lmnt.com/v1/ai/voice/list"

# 21 languages LMNT supports (ISO 639-1 codes from docs)
_LMNT_LANGUAGES = normalize_languages([
    "ar", "zh", "nl", "en", "fr", "de", "hi", "id",
    "it", "ja", "ko", "pl", "pt", "ru", "es", "sv",
    "th", "tr", "uk", "ur", "vi",
])

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
        voices_data = await self._fetch_voices()
        return SyncResult(
            stt_models=[],
            tts_models=self._derive_tts_models(),
            tts_voices=self._parse_voices(voices_data),
            source=self.source,
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

    def _derive_tts_models(self) -> list[SyncModel]:
        return [
            SyncModel(
                model_id="blizzard",
                display_name="LMNT Blizzard",
                type="tts",
                languages=_LMNT_LANGUAGES,
                streaming=True,
                is_default=True,
                description="LMNT's flagship TTS model — natural, expressive, high-quality voice synthesis.",
            ),
            SyncModel(
                model_id="lmnt-tts-0216",
                display_name="LMNT TTS 0216",
                type="tts",
                languages=_LMNT_LANGUAGES,
                streaming=True,
                is_default=False,
                description="Latency-optimized LMNT TTS variant for real-time use cases.",
            ),
        ]

    def _parse_voices(self, voices_data: list[dict]) -> list[SyncVoice]:
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
                languages=_LMNT_LANGUAGES,
                description=v.get("description") or None,
                preview_url=v.get("preview_url") or None,
                use_cases=use_cases,
                compatible_models=[],  # all voices work with all LMNT models
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
