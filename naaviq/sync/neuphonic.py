"""
Neuphonic sync script.

Source: api
  - TTS voices: GET /voices
  - TTS models: derived (no /models endpoint — one API model tier)
  - STT: not offered — stt_models=[]

Neuphonic is a real-time TTS provider with streaming via WebSocket and SSE.
Supports 6 languages: English, Spanish, German, Dutch, French, Hindi.

Auth: X-API-KEY header (plain API key, no prefix).
"""

from __future__ import annotations

import asyncio

import httpx

from naaviq.config import settings
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncModel, SyncResult, SyncVoice
from naaviq.sync.language import normalize_languages

_BASE_URL = "https://api.neuphonic.com"
_VOICES_PATH = "/voices"

_API_DOCS_URLS = [
    "https://docs.neuphonic.com/api-reference/voices/get-voices",
]

_MODEL_LANGUAGES = normalize_languages(["en", "es", "de", "nl", "fr", "hi"])

_TTS_MODELS: list[tuple[str, str, bool]] = [
    ("neuphonic", "Neuphonic", True),
]


class NeurophonicSyncer(ProviderSyncer):
    provider_id = "neuphonic"
    source = "api"

    async def sync(self) -> SyncResult:
        if not settings.neuphonic_api_key:
            raise ValueError("NEUPHONIC_API_KEY is not set in .env")

        voices_raw = await self._fetch_voices()
        tts_voices = self._parse_voices(voices_raw)
        all_langs = sorted({lang for v in tts_voices for lang in v.languages})

        return SyncResult(
            stt_models=[],
            tts_models=self._derive_tts_models(all_langs),
            tts_voices=tts_voices,
            source=self.source,
            api_urls=[_BASE_URL + _VOICES_PATH],
            docs_urls=_API_DOCS_URLS,
            notes=f"{len(tts_voices)} voices, {len(_TTS_MODELS)} model tier. TTS-only.",
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

    def _derive_tts_models(self, languages: list[str]) -> list[SyncModel]:
        langs = languages or _MODEL_LANGUAGES
        return [
            SyncModel(
                model_id=model_id,
                display_name=display_name,
                type="tts",
                languages=langs,
                streaming=True,
                is_default=is_default,
                description="Real-time TTS via WebSocket and SSE.",
            )
            for model_id, display_name, is_default in _TTS_MODELS
        ]

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
                compatible_models=[],
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
