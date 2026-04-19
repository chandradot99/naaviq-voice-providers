"""
Fish Audio sync script.

Source: mixed
  - TTS voices: GET https://api.fish.audio/model (paginated, auth required)
  - TTS models: AI-parsed from docs (no /models endpoint)
  - STT models: AI-parsed from docs (beta ASR endpoint)

Voice object shape (abbreviated):
  {
    "_id": "...",
    "type": "tts",
    "title": "Voice Name",
    "description": "...",
    "cover_image": "...",
    "tags": ["tag1", "tag2"],
    "languages": ["en", "zh"],
    "visibility": "public",
    "state": "trained",
    "like_count": 42,
    "task_count": 1000,
    "train_mode": "fast" | "full",
    "author": {"_id": "...", "nickname": "username", "avatar": "..."}
  }

Pagination: page_size + page_number query params.
Voices are fetched sorted by task_count (most used first), capped at _MAX_VOICES.

Auth: Authorization: Bearer API_KEY
Config: FISH_AUDIO_API_KEY
"""

from __future__ import annotations

import asyncio

import httpx

from naaviq.config import settings
from naaviq.sync.ai_parser import parse_models_from_docs
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncResult, SyncVoice
from naaviq.sync.language import normalize_languages

_BASE_URL = "https://api.fish.audio"
_MODELS_URL = f"{_BASE_URL}/model"

_DOCS_URLS = [
    "https://docs.fish.audio/developer-guide/models-pricing/models-overview",
    "https://docs.fish.audio/api-reference/endpoint/openapi-v1/text-to-speech",
    "https://docs.fish.audio/api-reference/endpoint/openapi-v1/speech-to-text",
]

_TTS_MODEL_GUIDANCE = """
Extract Fish Audio TTS models. There are 2 models.

1. model_id="s2-pro", display_name="Fish Audio S2 Pro", is_default=True, streaming=True
   - Flagship dual-autoregressive model. 80+ languages with automatic detection.
   - Tier 1 (highest quality): Japanese, English, Chinese.
   - Supports expression control via [bracket] syntax and multi-speaker dialogue.
   - description="Fish Audio's flagship TTS model — 80+ languages, ~100ms TTFA, expressive control."
   - languages: ["*"]

2. model_id="s1", display_name="Fish Audio S1", is_default=False, streaming=True
   - Previous generation. 13 languages.
   - description="Fish Audio S1 — 13 languages, streaming."
   - languages: ["en", "zh", "ja", "de", "fr", "es", "ko", "ar", "ru", "nl", "it", "pl", "pt"]

Use exact model_id values: "s2-pro" and "s1".
"""

_STT_MODEL_GUIDANCE = """
Extract Fish Audio STT models. There is 1 model (currently in beta).

1. model_id="fishaudio-asr", display_name="Fish Audio ASR", is_default=True, streaming=True
   - Beta ASR endpoint (POST /v1/asr).
   - Multilingual with automatic code-switching.
   - description="Fish Audio ASR (beta) — multilingual recognition with automatic code-switching."
   - languages: ["en", "zh", "ja", "ko"]

Use exact model_id value: "fishaudio-asr".
"""

_VOICE_PAGE_SIZE = 20
_MAX_VOICES = 200  # cap at top 200 most-used public TTS voices


class FishAudioSyncer(ProviderSyncer):
    provider_id = "fishaudio"
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
            api_urls=[_MODELS_URL],
            docs_urls=_DOCS_URLS,
            notes=sync_notes,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_voices(self) -> list[dict]:
        if not settings.fish_audio_api_key:
            raise ValueError("FISH_AUDIO_API_KEY is not set in .env")

        headers = {"Authorization": f"Bearer {settings.fish_audio_api_key}"}
        all_voices: list[dict] = []
        page = 1

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            while len(all_voices) < _MAX_VOICES:
                resp = await client.get(
                    _MODELS_URL,
                    headers=headers,
                    params={
                        "type": "tts",
                        "visibility": "public",
                        "sort_by": "task_count",
                        "page_size": _VOICE_PAGE_SIZE,
                        "page_number": page,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get("items") or []
                if not items:
                    break
                all_voices.extend(items)
                if len(all_voices) >= data.get("total", 0) or len(items) < _VOICE_PAGE_SIZE:
                    break
                page += 1

        return all_voices[:_MAX_VOICES]

    def _parse_voices(self, voices_data: list[dict]) -> list[SyncVoice]:
        voices: list[SyncVoice] = []
        for v in voices_data:
            voice_id = v.get("_id")
            if not voice_id:
                continue

            if v.get("state") != "trained":
                continue

            tags = [t.lower() for t in (v.get("tags") or [])]
            gender = None
            if "female" in tags:
                gender = "female"
            elif "male" in tags:
                gender = "male"

            raw_langs = v.get("languages") or []
            languages = normalize_languages(raw_langs) if raw_langs else ["*"]

            preview_url = None
            samples = v.get("samples") or []
            if samples and isinstance(samples[0], dict):
                preview_url = samples[0].get("url") or samples[0].get("audio")

            use_cases = [t for t in tags if t not in {"male", "female", "neutral"}]

            voices.append(SyncVoice(
                voice_id=voice_id,
                display_name=v.get("title") or voice_id,
                gender=gender,
                category="premade",
                languages=languages,
                description=(v.get("description") or None),
                preview_url=preview_url,
                use_cases=use_cases,
                compatible_models=[],  # all voices work with all Fish Audio TTS models
                meta={
                    "like_count": v.get("like_count"),
                    "task_count": v.get("task_count"),
                    "train_mode": v.get("train_mode"),
                    "author": (v.get("author") or {}).get("nickname"),
                    "cover_image": v.get("cover_image"),
                },
            ))
        return voices


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    syncer = FishAudioSyncer()
    try:
        result = await syncer.sync()
    except httpx.HTTPStatusError as e:
        print(f"\nFish Audio API error ({e.response.status_code}): {e.response.text[:300]}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== STT Models ({len(result.stt_models)}) ===")
    for m in result.stt_models:
        print(f"  {m.model_id!r:25} {m.display_name!r:30} langs={m.languages}")

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        print(
            f"  {m.model_id!r:12} {m.display_name!r:28} "
            f"langs={m.languages} is_default={m.is_default}"
        )

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) ===")
    for v in result.tts_voices[:20]:
        print(
            f"  {v.voice_id!r:36} {v.display_name!r:25} "
            f"lang={v.languages} gender={v.gender}"
        )
    if len(result.tts_voices) > 20:
        print(f"  ... and {len(result.tts_voices) - 20} more")

    print(f"\nSource : {result.source}")
    print(f"Notes  : {result.notes}")
    print(f"Fetched: {result.fetched_at}")


if __name__ == "__main__":
    asyncio.run(_main())
