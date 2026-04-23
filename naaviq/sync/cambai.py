"""
CAMB.ai sync script.

Source: mixed
  - TTS voices: GET https://client.camb.ai/apis/list-voices (no pagination, auth required)
  - TTS models: AI-parsed from docs (MARS8 model family)
  - STT models: AI-parsed from docs (transcription API)

Voice object shape:
  {
    "id": 147320,
    "voice_name": "Alice",
    "gender": 1,           -- 1=male, 2=female, 9=other/neutral
    "age": null,
    "language": 1,         -- internal integer language code (1–150)
    "description": null,
    "is_published": true
  }

Voice language is an internal integer — CAMB.ai supports cross-lingual synthesis
so all voices are set to ["*"] (all languages supported).

Auth: x-api-key header
Config: CAMBAI_API_KEY
"""

from __future__ import annotations

import asyncio

import httpx

from naaviq.config import settings
from naaviq.sync.ai_parser import parse_models_from_docs
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncResult, SyncVoice

_BASE_URL = "https://client.camb.ai"
_VOICES_URL = f"{_BASE_URL}/apis/list-voices"

_DOCS_URLS = [
    "https://docs.camb.ai/models",
    "https://docs.camb.ai/getting-started/quickstart",
    "https://docs.camb.ai/api-reference/endpoint/list-voices",
]

_TTS_MODEL_GUIDANCE = """
Extract CAMB.ai TTS models. There are 4 models in the MARS8 family.

1. model_id="mars-flash", display_name="MARS Flash", is_default=True, streaming=True
   - 600M parameter fast model. Sub-150ms latency. 140+ languages.
   - description="CAMB.ai MARS Flash — ultra-low latency TTS, 140+ languages."
   - languages: ["*"]

2. model_id="mars-pro", display_name="MARS Pro", is_default=False, streaming=True
   - 600M parameter high-quality model. 140+ languages.
   - description="CAMB.ai MARS Pro — high-quality TTS, 140+ languages."
   - languages: ["*"]

3. model_id="mars-instruct", display_name="MARS Instruct", is_default=False, streaming=True
   - 1.2B parameter instruction-following model. 140+ languages. Highest expressiveness.
   - description="CAMB.ai MARS Instruct — 1.2B param expressive TTS, instruction-following."
   - languages: ["*"]

4. model_id="mars-8.1-pro-beta", display_name="MARS 8.1 Pro Beta", is_default=False, streaming=True
   - Pro beta variant with latest improvements. 140+ languages.
   - description="CAMB.ai MARS 8.1 Pro Beta — latest pro improvements, 140+ languages."
   - languages: ["*"]

Use exact model_id values as listed above.
"""

_STT_MODEL_GUIDANCE = """
Extract CAMB.ai STT (transcription) models. There is 1 model.

1. model_id="camb-transcribe", display_name="CAMB Transcribe", is_default=True, streaming=False
   - Async transcription. Returns word-level timestamps, speaker diarization.
   - Output formats: txt, srt, vtt, json.
   - description="CAMB.ai transcription — async STT with word timestamps and diarization."
   - languages: ["*"]

Use exact model_id: "camb-transcribe".
"""

_GENDER_MAP = {1: "male", 2: "female", 9: "neutral"}


class CambAISyncer(ProviderSyncer):
    provider_id = "cambai"
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
        if not settings.cambai_api_key:
            raise ValueError("CAMBAI_API_KEY is not set in .env")

        headers = {"x-api-key": settings.cambai_api_key}
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(_VOICES_URL, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else (data.get("voices") or data.get("data") or [])

    def _parse_voices(self, voices_data: list[dict]) -> list[SyncVoice]:
        voices: list[SyncVoice] = []
        for v in voices_data:
            voice_id = v.get("id")
            if voice_id is None:
                continue

            gender = _GENDER_MAP.get(v.get("gender"))

            age_raw = v.get("age")
            age = _age_label(age_raw) if age_raw else None

            voices.append(SyncVoice(
                voice_id=str(voice_id),
                display_name=v.get("voice_name") or str(voice_id),
                gender=gender,
                category="premade",
                languages=["*"],  # cross-lingual: all voices work with all 140+ languages
                age=age,
                description=v.get("description") or None,
                compatible_models=["*"],  # all voices work with all MARS models
                meta={"language_code": v.get("language")},
            ))
        return voices


# ── Helpers ───────────────────────────────────────────────────────────────────

def _age_label(age: int) -> str | None:
    if age < 18:
        return "child"
    if age < 30:
        return "young_adult"
    if age < 50:
        return "adult"
    return "mature"


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    syncer = CambAISyncer()
    try:
        result = await syncer.sync()
    except httpx.HTTPStatusError as e:
        print(f"\nCAMB.ai API error ({e.response.status_code}): {e.response.text[:300]}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== STT Models ({len(result.stt_models)}) ===")
    for m in result.stt_models:
        print(f"  {m.model_id!r:20} {m.display_name!r:25} langs={m.languages}")

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        print(
            f"  {m.model_id!r:22} {m.display_name!r:22} "
            f"langs={m.languages} is_default={m.is_default}"
        )

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) ===")
    for v in result.tts_voices[:20]:
        print(
            f"  {v.voice_id!r:8} {v.display_name!r:20} "
            f"gender={v.gender or '?':7} age={v.age or '?'}"
        )
    if len(result.tts_voices) > 20:
        print(f"  ... and {len(result.tts_voices) - 20} more")

    print(f"\nSource : {result.source}")
    print(f"Notes  : {result.notes}")
    print(f"Fetched: {result.fetched_at}")


if __name__ == "__main__":
    asyncio.run(_main())
