"""
Neuphonic sync script.

Source: mixed
  - TTS voices: GET /voices (API)
  - TTS models: AI-parsed from docs (no /models endpoint)
  - STT: not offered — stt_models=[]

Neuphonic is a real-time TTS provider with streaming via WebSocket and SSE.

Auth: X-API-KEY header (plain API key, no prefix).
"""

from __future__ import annotations

import asyncio

import httpx

from naaviq.config import settings
from naaviq.sync.ai_parser import parse_models_from_docs
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncResult, SyncVoice
from naaviq.sync.language import normalize_languages

_BASE_URL = "https://api.neuphonic.com"
_VOICES_PATH = "/voices"

_DOCS_URLS = [
    "https://docs.neuphonic.com",
    "https://docs.neuphonic.com/build-group/text-to-speech",
]

_MODEL_GUIDANCE = """
Extract Neuphonic TTS models. There is 1 model.

model_id="neuphonic", display_name="Neuphonic", is_default=True, streaming=True
Extract the list of supported languages from the docs.
description="Real-time TTS via WebSocket and SSE."
"""


class NeurophonicSyncer(ProviderSyncer):
    provider_id = "neuphonic"
    source = "mixed"

    async def sync(self) -> SyncResult:
        if not settings.neuphonic_api_key:
            raise ValueError("NEUPHONIC_API_KEY is not set in .env")

        voices_raw, (tts_models, notes) = await asyncio.gather(
            self._fetch_voices(),
            parse_models_from_docs(
                seed_urls=_DOCS_URLS,
                provider_id=self.provider_id,
                model_type="tts",
                guidance=_MODEL_GUIDANCE,
            ),
        )
        tts_voices = self._parse_voices(voices_raw)

        from_cache = isinstance(notes, dict) and notes.get("source") == "cache"
        sync_notes = (
            f"{len(tts_voices)} voices. {len(tts_models)} TTS model (cache)."
            if from_cache else
            f"{len(tts_voices)} voices. {len(tts_models)} TTS model."
        )

        return SyncResult(
            stt_models=[],
            tts_models=tts_models,
            tts_voices=tts_voices,
            source=self.source,
            api_urls=[_BASE_URL + _VOICES_PATH],
            docs_urls=_DOCS_URLS,
            notes=sync_notes,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_voices(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(
                _BASE_URL + _VOICES_PATH,
                headers={"X-API-KEY": settings.neuphonic_api_key},
            )
            resp.raise_for_status()
            data = resp.json()
            return (data.get("data") or {}).get("voices") or []

    def _parse_voices(self, voices: list[dict]) -> list[SyncVoice]:
        result = []
        for v in voices:
            voice_id = v.get("voice_id", "")
            name = v.get("name") or voice_id
            lang = v.get("lang_code", "")
            tags: list[str] = v.get("tags") or []
            tags_lower = {t.lower() for t in tags}

            gender = None
            if "female" in tags_lower:
                gender = "female"
            elif "male" in tags_lower:
                gender = "male"

            style_tags = [t for t in tags if t.lower() not in ("male", "female")]

            result.append(SyncVoice(
                voice_id=voice_id,
                display_name=name,
                gender=gender,
                category="premade",
                languages=normalize_languages([lang]) if lang else [],
                description=", ".join(style_tags) if style_tags else None,
                compatible_models=["*"],
            ))
        return result


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    syncer = NeurophonicSyncer()
    try:
        result = await syncer.sync()
    except (ValueError, httpx.HTTPStatusError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        marker = " [default]" if m.is_default else ""
        print(f"  {m.model_id!r:20} {m.display_name!r:20} langs={m.languages}{marker}")

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) ===")
    for v in result.tts_voices:
        print(
            f"  {v.voice_id!r:40} {v.display_name!r:25} "
            f"lang={v.languages} gender={v.gender}"
        )

    print(f"\nSource : {result.source}")
    print(f"Notes  : {result.notes}")
    print(f"Fetched: {result.fetched_at}")


if __name__ == "__main__":
    asyncio.run(_main())
