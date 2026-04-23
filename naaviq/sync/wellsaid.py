"""
WellSaid Labs sync script.

Source: mixed
  - TTS voices: GET https://api.wellsaidlabs.com/v1/tts/avatars (no pagination, auth required)
  - TTS models: AI-parsed from docs
  - STT: not offered — stt_models=[]

Voice object shape:
  {
    "speaker_id": 42,
    "name": "Alana B.",
    "gender": "Female",
    "language": "English",
    "style": "Conversational",
    "accent": "American"
  }

Auth: X-API-KEY header
Config: WELLSAID_API_KEY
"""

from __future__ import annotations

import asyncio

import httpx

from naaviq.config import settings
from naaviq.sync.ai_parser import parse_models_from_docs
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncResult, SyncVoice
from naaviq.sync.language import normalize_languages

_BASE_URL = "https://api.wellsaidlabs.com/v1"
_AVATARS_URL = f"{_BASE_URL}/tts/avatars"

_DOCS_URLS = [
    "https://docs.wellsaidlabs.com/docs/getting-started",
    "https://docs.wellsaidlabs.com/reference/model-selection-with-the-api",
    "https://docs.wellsaidlabs.com/reference/available-voice-avatars",
]

_TTS_MODEL_GUIDANCE = """
Extract WellSaid Labs TTS models. There are 2 models.

1. model_id="caruso", display_name="Caruso", is_default=True, streaming=True
   - Newest flagship model. English only. 30% faster rendering.
   - Supports AI Director features: pitch, pace, emotional intonation control.
   - description="WellSaid Caruso — fastest, highest-quality English TTS with AI Director control."
   - languages: ["en"]

2. model_id="legacy", display_name="Legacy", is_default=False, streaming=True
   - Broader language support: 15+ languages, 145 voices.
   - description="WellSaid Legacy — 15+ languages, 145 voices."
   - languages: ["en", "ar", "zh", "da", "nl", "fr", "de", "it", "ja", "ko", "fa", "pl", "pt", "es", "sv", "tr"]

Use exact model_id values: "caruso" and "legacy".
"""

_GENDER_MAP = {"male": "male", "female": "female"}

_LANG_NAME_TO_BCP47: dict[str, str] = {
    "english":    "en",
    "arabic":     "ar",
    "chinese":    "zh",
    "cantonese":  "zh",
    "mandarin":   "zh",
    "danish":     "da",
    "dutch":      "nl",
    "french":     "fr",
    "german":     "de",
    "italian":    "it",
    "japanese":   "ja",
    "korean":     "ko",
    "persian":    "fa",
    "polish":     "pl",
    "portuguese": "pt",
    "spanish":    "es",
    "swedish":    "sv",
    "turkish":    "tr",
}

_ACCENT_MAP: dict[str, str] = {
    "american":       "american",
    "north american": "american",
    "united states":  "american",
    "british":        "british",
    "united kingdom": "british",
    "australia":      "australian",
    "australian":     "australian",
    "canada":         "canadian",
    "canadian":       "canadian",
    "ireland":        "irish",
    "irish":          "irish",
    "scotland":       "scottish",
    "scottish":       "scottish",
    "new zealand":    "new_zealander",
    "south africa":   "south_african",
    "south african":  "south_african",
}


class WellSaidSyncer(ProviderSyncer):
    provider_id = "wellsaid"
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
            api_urls=[_AVATARS_URL],
            docs_urls=_DOCS_URLS,
            notes=sync_notes,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_voices(self) -> list[dict]:
        if not settings.wellsaid_api_key:
            raise ValueError("WELLSAID_API_KEY is not set in .env")

        headers = {"X-API-KEY": settings.wellsaid_api_key}
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(_AVATARS_URL, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data.get("avatars") or (data if isinstance(data, list) else [])

    def _parse_voices(self, voices_data: list[dict]) -> list[SyncVoice]:
        voices: list[SyncVoice] = []
        for v in voices_data:
            # API returns "id" (int) as the voice identifier
            vid = v.get("id") or v.get("speaker_id")
            if vid is None:
                continue

            voice_id = str(vid)
            gender = _GENDER_MAP.get((v.get("gender") or "").lower())

            # Use locale ("en_US") if present, else fall back to language name
            locale = v.get("locale")
            if locale:
                languages = normalize_languages([locale.replace("_", "-")])
            else:
                lang_name = (v.get("language") or "english").lower()
                bcp47 = _LANG_NAME_TO_BCP47.get(lang_name, "en")
                languages = normalize_languages([bcp47])

            # accent_type is e.g. "English (United States)" — extract the part in parens
            accent_type = v.get("accent_type") or ""
            accent_key = accent_type.split("(")[-1].rstrip(")").strip().lower() if "(" in accent_type else accent_type.lower()
            accent = _ACCENT_MAP.get(accent_key)

            style = (v.get("style") or "").lower()
            use_cases = [style] if style else []

            lang_code = languages[0].split("-")[0] if languages else "en"
            compatible_models = ["caruso"] if lang_code == "en" else ["legacy"]

            voices.append(SyncVoice(
                voice_id=voice_id,
                display_name=v.get("name") or voice_id,
                gender=gender,
                category="premade",
                languages=languages,
                accent=accent,
                use_cases=use_cases,
                preview_url=v.get("preview_audio") or None,
                compatible_models=compatible_models,
                meta={"style": v.get("style"), "characteristics": v.get("characteristics")},
            ))
        return voices


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys
    from collections import Counter

    syncer = WellSaidSyncer()
    try:
        result = await syncer.sync()
    except httpx.HTTPStatusError as e:
        print(f"\nWellSaid API error ({e.response.status_code}): {e.response.text[:300]}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        print(f"  {m.model_id!r:10} {m.display_name!r:12} langs={m.languages} is_default={m.is_default}")

    model_counts = Counter(
        (v.compatible_models[0] if v.compatible_models else "any")
        for v in result.tts_voices
    )
    print(f"\n=== TTS Voices ({len(result.tts_voices)}) — by model ===")
    for model_id, count in sorted(model_counts.items()):
        print(f"  {model_id!r:10} {count} voices")

    print("\n=== Sample voices (first 10) ===")
    for v in result.tts_voices[:10]:
        print(
            f"  {v.voice_id!r:6} {v.display_name!r:20} "
            f"gender={v.gender or '?':6} accent={v.accent or '?':12} langs={v.languages}"
        )

    print(f"\nSource : {result.source}")
    print(f"Notes  : {result.notes}")
    print(f"Fetched: {result.fetched_at}")


if __name__ == "__main__":
    asyncio.run(_main())
