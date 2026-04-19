"""
Lovo AI (Genny) sync script.

Source: mixed
  - TTS voices: GET https://api.genny.lovo.ai/api/v1/speakers (paginated, auth required)
  - TTS models: AI-parsed from docs
  - STT: not offered — stt_models=[]

Voice object shape:
  {
    "id": "640f477d2babeb0024be422b",
    "displayName": "Voice Name",
    "gender": "male" | "female",
    "locale": "en-US",
    "speakerType": "pro-v2" | "pro" | ...,
    "ageRange": "adult" | "young adult" | ...,
    "sampleTtsUrl": "https://..."
  }

Pagination: ?page=<skip>&limit=<per_page> (page = number of pages to skip from 0).
Auth: X-API-KEY header
Config: LOVO_API_KEY
"""

from __future__ import annotations

import asyncio

import httpx

from naaviq.config import settings
from naaviq.sync.ai_parser import parse_models_from_docs
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncResult, SyncVoice
from naaviq.sync.language import normalize_languages

_BASE_URL = "https://api.genny.lovo.ai/api/v1"
_SPEAKERS_URL = f"{_BASE_URL}/speakers"

_DOCS_URLS = [
    "https://docs.genny.lovo.ai/reference/intro/getting-started",
    "https://lovo.ai/post/introducing-pro-v2-voices-directable-text-to-speech-with-natural-language",
]

_TTS_MODEL_GUIDANCE = """
Extract Lovo AI (Genny) TTS models. There are 2 models.

1. model_id="pro-v2", display_name="Lovo Pro V2", is_default=True, streaming=False
   - Most expressive model. Supports natural language directives (tone, speed, accent, emotions).
   - 100+ languages, 30 emotion presets.
   - description="Lovo Pro V2 — ultra-realistic, directable TTS with 100+ languages and emotion control."
   - languages: ["*"]

2. model_id="pro", display_name="Lovo Pro", is_default=False, streaming=False
   - Ultra-realistic English voices, indistinguishable from human recordings.
   - description="Lovo Pro — ultra-realistic English TTS."
   - languages: ["en"]

Use exact model_id values: "pro-v2" and "pro".
"""

_VOICE_PAGE_LIMIT = 100
_GENDER_MAP = {"male": "male", "female": "female"}


class LovoAISyncer(ProviderSyncer):
    provider_id = "lovoai"
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
        tts_voices = self._parse_voices(voices_data)

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
            api_urls=[_SPEAKERS_URL],
            docs_urls=_DOCS_URLS,
            notes=sync_notes,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_voices(self) -> list[dict]:
        if not settings.lovo_api_key:
            raise ValueError("LOVO_API_KEY is not set in .env")

        headers = {"X-API-KEY": settings.lovo_api_key}
        all_voices: list[dict] = []
        page = 0

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            while True:
                resp = await client.get(
                    _SPEAKERS_URL,
                    headers=headers,
                    params={"page": page, "limit": _VOICE_PAGE_LIMIT, "sort": "displayName:1"},
                )
                resp.raise_for_status()
                data = resp.json()
                items = (data.get("data") or data) if isinstance(data, dict) else data
                if not isinstance(items, list) or not items:
                    break
                all_voices.extend(items)
                if len(items) < _VOICE_PAGE_LIMIT:
                    break
                page += 1

        # Deduplicate — Lovo's paginated API returns overlapping results
        seen: set[str] = set()
        unique: list[dict] = []
        for v in all_voices:
            vid = v.get("id")
            if vid and vid not in seen:
                seen.add(vid)
                unique.append(v)
        return unique

    def _parse_voices(self, voices_data: list[dict]) -> list[SyncVoice]:
        voices: list[SyncVoice] = []
        for v in voices_data:
            voice_id = v.get("id")
            if not voice_id:
                continue

            gender = _GENDER_MAP.get((v.get("gender") or "").lower())
            languages = normalize_languages([v["locale"]]) if v.get("locale") else ["*"]

            speaker_type = (v.get("speakerType") or "").lower()
            if "v2" in speaker_type or "pro-v2" in speaker_type:
                compatible_models = ["pro-v2"]
            elif "pro" in speaker_type:
                compatible_models = ["pro"]
            else:
                compatible_models = []

            age_raw = (v.get("ageRange") or "").lower().replace(" ", "_")
            age = age_raw if age_raw else None

            voices.append(SyncVoice(
                voice_id=voice_id,
                display_name=v.get("displayName") or voice_id,
                gender=gender,
                category="premade",
                languages=languages,
                age=age,
                preview_url=v.get("sampleTtsUrl") or None,
                compatible_models=compatible_models,
                meta={"speaker_type": v.get("speakerType")},
            ))
        return voices


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys
    from collections import Counter

    syncer = LovoAISyncer()
    try:
        result = await syncer.sync()
    except httpx.HTTPStatusError as e:
        print(f"\nLovo AI error ({e.response.status_code}): {e.response.text[:300]}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        print(
            f"  {m.model_id!r:12} {m.display_name!r:20} "
            f"langs={m.languages} is_default={m.is_default}"
        )

    model_counts = Counter(
        (v.compatible_models[0] if v.compatible_models else "any")
        for v in result.tts_voices
    )
    print(f"\n=== TTS Voices ({len(result.tts_voices)}) — by model ===")
    for model_id, count in sorted(model_counts.items()):
        print(f"  {model_id!r:12} {count} voices")

    print(f"\n=== Sample voices (first 10) ===")
    for v in result.tts_voices[:10]:
        print(
            f"  {v.voice_id!r:30} {v.display_name!r:25} "
            f"gender={v.gender or '?':6} langs={v.languages}"
        )

    print(f"\nSource : {result.source}")
    print(f"Notes  : {result.notes}")
    print(f"Fetched: {result.fetched_at}")


if __name__ == "__main__":
    asyncio.run(_main())
