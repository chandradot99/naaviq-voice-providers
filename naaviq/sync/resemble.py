"""
Resemble AI sync script.

Source: mixed
  - TTS voices: GET /api/v2/voices (paginated, API)
  - TTS models: AI-parsed from docs (3 models — Chatterbox, Chatterbox Multilingual, Chatterbox-Turbo)
  - STT: basic file-upload transcription only, no model tiers — stt_models=[]

Auth: Authorization: Bearer <api_key>
Base URL: https://app.resemble.ai/api/v2

Voices are paginated (page + page_size). All pages are fetched and merged.
"""

from __future__ import annotations

import asyncio

import httpx

from naaviq.config import settings
from naaviq.sync.ai_parser import parse_models_from_docs
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncModel, SyncResult, SyncVoice
from naaviq.sync.language import normalize_languages

_BASE_URL = "https://app.resemble.ai/api/v2"
_VOICES_PATH = "/voices"
_PAGE_SIZE = 100

_DOCS_URLS = [
    "https://docs.resemble.ai/docs/text-to-speech",
    "https://docs.resemble.ai/reference/voices_list",
]

_MODEL_GUIDANCE = """
Extract Resemble AI TTS models. There are exactly 3 models.

1. model_id="chatterbox", display_name="Chatterbox", is_default=True, streaming=True
   - Standard TTS model. English-focused.
   - languages=["en"]
   - description="High-quality TTS with voice cloning. Streaming supported."

2. model_id="chatterbox-multilingual", display_name="Chatterbox Multilingual", is_default=False, streaming=True
   - Multilingual version of Chatterbox.
   - Extract the list of supported languages from the docs. If not listed, use languages=["*"].
   - description="Multilingual TTS with voice cloning. Streaming supported."

3. model_id="chatterbox-turbo", display_name="Chatterbox Turbo", is_default=False, streaming=True
   - Lower latency variant with paralinguistic support (laughter, sighs, etc.).
   - languages=["en"]
   - description="Low-latency TTS with paralinguistic expression support. Streaming supported."

Use exact model_id values as listed above.
"""

_GENDER_MAP = {"male": "male", "female": "female"}


class ResembleSyncer(ProviderSyncer):
    provider_id = "resemble"
    source = "mixed"

    async def sync(self) -> SyncResult:
        if not settings.resemble_api_key:
            raise ValueError("RESEMBLE_API_KEY is not set in .env")

        voices_raw, (tts_models, notes) = await asyncio.gather(
            self._fetch_all_voices(),
            parse_models_from_docs(
                seed_urls=_DOCS_URLS,
                provider_id=self.provider_id,
                model_type="tts",
                guidance=_MODEL_GUIDANCE,
            ),
        )

        tts_voices = self._parse_voices(voices_raw)
        model_ids = [m.model_id for m in tts_models]

        from_cache = isinstance(notes, dict) and notes.get("source") == "cache"
        if from_cache:
            sync_notes = f"{len(tts_voices)} voices. {len(tts_models)} TTS models (cache)."
        else:
            sync_notes = (
                f"{len(tts_voices)} voices. {len(tts_models)} TTS models. "
                f"Tokens: {notes.get('input_tokens', 0)}↑ {notes.get('output_tokens', 0)}↓"
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

    async def _fetch_all_voices(self) -> list[dict]:
        headers = {"Authorization": f"Bearer {settings.resemble_api_key}"}
        all_voices: list[dict] = []
        page = 1

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            while True:
                resp = await client.get(
                    _BASE_URL + _VOICES_PATH,
                    headers=headers,
                    params={"page": page, "page_size": _PAGE_SIZE},
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get("items") or []
                all_voices.extend(items)
                if page >= data.get("num_pages", 1):
                    break
                page += 1

        return all_voices

    def _parse_voices(self, voices: list[dict]) -> list[SyncVoice]:
        result = []
        for v in voices:
            voice_id = v.get("uuid") or v.get("id", "")
            name = v.get("name") or voice_id
            gender_raw = (v.get("gender") or "").lower()
            lang = v.get("language") or "en"

            result.append(SyncVoice(
                voice_id=voice_id,
                display_name=name,
                gender=_GENDER_MAP.get(gender_raw),
                category="premade",
                languages=normalize_languages([lang]),
                compatible_models=["*"],
            ))
        return result


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    syncer = ResembleSyncer()
    try:
        result = await syncer.sync()
    except (ValueError, httpx.HTTPStatusError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        marker = " [default]" if m.is_default else ""
        print(f"  {m.model_id!r:30} {m.display_name!r:30} langs={m.languages}{marker}")

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
