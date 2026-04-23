"""
MiniMax sync script.

Source: mixed
  - TTS voices: POST https://api.minimax.io/v1/get_voice (system voices, API)
  - TTS models: AI-parsed from docs (no /models endpoint)
  - STT: not offered — stt_models=[]

332 system voices across English, Chinese, Japanese, Korean, Spanish, Portuguese,
French, Indonesian, German, Russian, Italian, Dutch, Vietnamese, Arabic, Turkish,
Ukrainian, Thai, Polish, Romanian, Greek, Czech, Finnish, Hindi, and Cantonese.

Voice names follow the pattern "{Language}_{Description}" (e.g., "English_Graceful_Lady",
"Chinese (Mandarin)_HK_Flight_Attendant"). Language is extracted from this prefix.
Gender is inferred from description tags when present.

Auth: Authorization: Bearer <api_key>, MINIMAX_API_KEY env var.
"""

from __future__ import annotations

import asyncio

import httpx

from naaviq.config import settings
from naaviq.sync.ai_parser import parse_models_from_docs
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncResult, SyncVoice
from naaviq.sync.language import normalize_languages

_VOICES_URL = "https://api.minimax.io/v1/get_voice"

_DOCS_URLS = [
    "https://platform.minimax.io/docs/guides/models-intro",
    "https://platform.minimax.io/docs/api-reference/speech-t2a-http.md",
]

_MODEL_GUIDANCE = """
Extract MiniMax TTS models. There are 8 models ordered newest to oldest.
speech-2.8-hd is the flagship default.

Models (newest → oldest):
  speech-2.8-hd    "Speech 2.8 HD"    is_default=True  — 40+ languages, 7 emotions, streaming+async
  speech-2.8-turbo "Speech 2.8 Turbo" is_default=False — 40+ languages, 7 emotions, streaming+async
  speech-2.6-hd    "Speech 2.6 HD"    is_default=False — 40+ languages, 7 emotions, streaming+async
  speech-2.6-turbo "Speech 2.6 Turbo" is_default=False — 40+ languages, 7 emotions, streaming+async
  speech-02-hd     "Speech 02 HD"     is_default=False — 24 languages, 7 emotions, streaming+async
  speech-02-turbo  "Speech 02 Turbo"  is_default=False — 24 languages, 7 emotions, streaming+async
  speech-01-hd     "Speech 01 HD"     is_default=False — 24 languages, 7 emotions, streaming+async
  speech-01-turbo  "Speech 01 Turbo"  is_default=False — 24 languages, 7 emotions, streaming+async

For speech-2.x models extract the full language list from the docs language_boost parameter.
For speech-0x models use the smaller language list (zh, en, ar, ru, es, fr, pt, de, tr, nl, uk, vi, id, ja, it, ko, th, pl, ro, el, cs, fi, hi).
All models have streaming=True.
"""

# Voice name prefix → BCP-47 language code
_LANG_PREFIX_MAP: dict[str, str] = {
    "english":            "en",
    "chinese (mandarin)": "zh",
    "chinese":            "zh",
    "cantonese":          "yue",
    "japanese":           "ja",
    "korean":             "ko",
    "spanish":            "es",
    "portuguese":         "pt",
    "french":             "fr",
    "indonesian":         "id",
    "german":             "de",
    "russian":            "ru",
    "italian":            "it",
    "dutch":              "nl",
    "vietnamese":         "vi",
    "arabic":             "ar",
    "turkish":            "tr",
    "ukrainian":          "uk",
    "thai":               "th",
    "polish":             "pl",
    "romanian":           "ro",
    "greek":              "el",
    "czech":              "cs",
    "finnish":            "fi",
    "hindi":              "hi",
}

# Keywords that appear as whole words in voice_id (case-insensitive)
_MALE_WORDS   = {"male", "man", "boy", "guy", "bloke", "uncle", "father", "dad", "king", "prince", "brother", "son"}
_FEMALE_WORDS = {"female", "woman", "girl", "lady", "aunt", "mother", "mom", "queen", "princess", "sister", "daughter"}


class MinimaxSyncer(ProviderSyncer):
    provider_id = "minimax"
    source = "mixed"

    async def sync(self) -> SyncResult:
        if not settings.minimax_api_key:
            raise ValueError("MINIMAX_API_KEY is not set in .env")

        voices_data, (tts_models, notes) = await asyncio.gather(
            self._fetch_voices(),
            parse_models_from_docs(
                seed_urls=_DOCS_URLS,
                provider_id=self.provider_id,
                model_type="tts",
                guidance=_MODEL_GUIDANCE,
            ),
        )
        tts_voices = self._parse_voices(voices_data)

        from_cache = isinstance(notes, dict) and notes.get("source") == "cache"
        sync_notes = (
            f"{len(tts_voices)} system voices. {len(tts_models)} TTS models (cache)."
            if from_cache else
            f"{len(tts_voices)} system voices. {len(tts_models)} TTS models. "
            f"Tokens: {notes.get('input_tokens', 0)}↑ {notes.get('output_tokens', 0)}↓"
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
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(
                _VOICES_URL,
                headers={"Authorization": f"Bearer {settings.minimax_api_key}"},
                json={"voice_type": "system"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("system_voice") or []

    def _parse_voices(self, voices_data: list[dict]) -> list[SyncVoice]:
        voices = []
        for item in voices_data:
            voice_id = item.get("voice_id", "")
            voice_name = item.get("voice_name") or voice_id
            descriptions: list[str] = item.get("description") or []

            # Language prefix lives in voice_id (e.g. "English_radiant_girl"), not voice_name
            language = _language_from_name(voice_id)
            languages = normalize_languages([language]) if language else []
            # Gender clues are in the voice_id name, descriptions contain style tags
            gender = _gender_from_name(voice_id) or _gender_from_descriptions(descriptions)

            voices.append(SyncVoice(
                voice_id=voice_id,
                display_name=_display_name(voice_name or voice_id),
                gender=gender,
                category="premade",
                languages=languages,
                description=", ".join(descriptions) if descriptions else None,
                compatible_models=["*"],
            ))
        return voices


# ── Helpers ───────────────────────────────────────────────────────────────────

def _language_from_name(voice_name: str) -> str | None:
    lower = voice_name.lower()
    for prefix, lang in _LANG_PREFIX_MAP.items():
        if lower.startswith(prefix):
            return lang
    return None


def _gender_from_name(voice_id: str) -> str | None:
    words = {w.lower() for w in voice_id.replace("-", "_").split("_")}
    if words & _FEMALE_WORDS:
        return "female"
    if words & _MALE_WORDS:
        return "male"
    return None


def _gender_from_descriptions(descriptions: list[str]) -> str | None:
    words = {w.lower() for d in descriptions for w in d.replace("-", " ").split()}
    if words & _FEMALE_WORDS:
        return "female"
    if words & _MALE_WORDS:
        return "male"
    return None


def _display_name(voice_name: str) -> str:
    # "English_Graceful_Lady" → "Graceful Lady"
    # "Chinese (Mandarin)_HK_Flight_Attendant" → "HK Flight Attendant"
    parts = voice_name.split("_", 1)
    if len(parts) == 2:
        return parts[1].replace("_", " ")
    return voice_name.replace("_", " ")


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    from naaviq.sync.base import SyncResult  # noqa

    syncer = MinimaxSyncer()
    try:
        result = await syncer.sync()
    except (ValueError, httpx.HTTPStatusError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        marker = " [default]" if m.is_default else ""
        print(f"  {m.model_id!r:22} {m.display_name!r:22} langs={len(m.languages)}{marker}")

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) — first 20 ===")
    for v in result.tts_voices[:20]:
        print(
            f"  {v.voice_id!r:50} {v.display_name!r:30} "
            f"lang={v.languages} gender={v.gender}"
        )
    if len(result.tts_voices) > 20:
        print(f"  ... and {len(result.tts_voices) - 20} more")

    print(f"\nSource : {result.source}")
    print(f"Notes  : {result.notes}")
    print(f"Fetched: {result.fetched_at}")


if __name__ == "__main__":
    asyncio.run(_main())
