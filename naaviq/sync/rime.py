"""
Rime AI sync script.

Source: api
  - TTS voices: GET https://users.rime.ai/data/voices/voice_details.json (public, no auth)
  - TTS models: derived synthetically — no /models endpoint
  - STT: not offered — stt_models=[]

Voice catalog is a static JSON file returning all 600+ voices at once (no pagination).

Voice object shape:
  {
    "speaker": "albion",
    "gender": "Male",           -- "Male", "Female", "Non-binary", or ""
    "age": "Young Adult",       -- "Young Adult", "Adult", "Elder", or ""
    "country": "England",
    "dialect": "English",
    "demographic": "White",
    "genre": ["Any"],           -- use case tags; may be comma-separated string inside array
    "modelId": "arcana",        -- "arcana", "mistv3", "mistv2", "mist"
    "lang": "eng",              -- ISO 639-2 three-letter code
    "language": "English",
    "flagship": true
  }

TTS models (derived synthetically — no API endpoint):
  arcana  : flagship, default, 9 languages (en/es/fr/de/ar/he/hi/ja/pt), streaming
  mistv3  : ultra-low latency (<100ms TTFB), 4 languages (en/es/fr/de), streaming
  mistv2  : high-volume production, 4 languages (en/es/fr/de), streaming
  mist    : legacy, English-only, streaming

Auth: Bearer token for synthesis only — voice list endpoint is public.
Config: RIME_API_KEY (not needed for sync, reserved for future synthesis use).
"""

from __future__ import annotations

import asyncio

import httpx

from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncModel, SyncResult, SyncVoice
from naaviq.sync.language import normalize_languages

_VOICES_URL = "https://users.rime.ai/data/voices/voice_details.json"

# ISO 639-2 (3-letter) → BCP-47 (2-letter)
_ISO3_TO_BCP47: dict[str, str] = {
    "eng": "en",
    "spa": "es",
    "fra": "fr",
    "ger": "de",
    "ara": "ar",
    "heb": "he",
    "hin": "hi",
    "jpn": "ja",
    "por": "pt",
    "tam": "ta",
}

# Gender normalization
_GENDER_MAP: dict[str, str] = {
    "male":       "male",
    "female":     "female",
    "non-binary": "neutral",
}

# Country → accent (English voices only; other languages skip accent)
_COUNTRY_TO_ACCENT: dict[str, str] = {
    "england":     "british",
    "uk":          "british",
    "us":          "american",
    "usa":         "american",
    "australia":   "australian",
    "india":       "indian",
    "ireland":     "irish",
    "canada":      "canadian",
    "new zealand": "new_zealander",
    "south africa": "south_african",
}

# Per-model language coverage (derived from actual voice data + docs)
_MODEL_LANGUAGES: dict[str, list[str]] = {
    "arcana":  normalize_languages(["en", "es", "fr", "de", "ar", "he", "hi", "ja", "pt"]),
    "mistv3":  normalize_languages(["en", "es", "fr", "de"]),
    "mistv2":  normalize_languages(["en", "es", "fr", "de"]),
    "mist":    normalize_languages(["en"]),
}


class RimeSyncer(ProviderSyncer):
    provider_id = "rime"
    source = "api"

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
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(_VOICES_URL)
            resp.raise_for_status()
            return resp.json()

    def _derive_tts_models(self) -> list[SyncModel]:
        return [
            SyncModel(
                model_id="arcana",
                display_name="Rime Arcana",
                type="tts",
                languages=_MODEL_LANGUAGES["arcana"],
                streaming=True,
                is_default=True,
                description="Rime's flagship TTS model — ultra-realistic, expressive, multilingual.",
            ),
            SyncModel(
                model_id="mistv3",
                display_name="Rime Mist v3",
                type="tts",
                languages=_MODEL_LANGUAGES["mistv3"],
                streaming=True,
                is_default=False,
                description="Ultra-low latency (<100ms TTFB) with deterministic pronunciation control.",
            ),
            SyncModel(
                model_id="mistv2",
                display_name="Rime Mist v2",
                type="tts",
                languages=_MODEL_LANGUAGES["mistv2"],
                streaming=True,
                is_default=False,
                description="High-volume production model with deterministic pronunciation.",
            ),
            SyncModel(
                model_id="mist",
                display_name="Rime Mist",
                type="tts",
                languages=_MODEL_LANGUAGES["mist"],
                streaming=True,
                is_default=False,
                description="Legacy Mist model — English only.",
            ),
        ]

    def _parse_voices(self, voices_data: list[dict]) -> list[SyncVoice]:
        # Group by speaker — some voices appear in multiple models with the same or
        # different language. Deduplicate into one record per speaker, collecting
        # all model IDs and unioning all languages.
        from collections import defaultdict
        by_speaker: dict[str, list[dict]] = defaultdict(list)
        for v in voices_data:
            if v.get("speaker"):
                by_speaker[v["speaker"]].append(v)

        voices: list[SyncVoice] = []
        for speaker, entries in by_speaker.items():
            # Use the entry from the most capable model as the primary source of metadata
            _MODEL_RANK = {"arcana": 0, "mistv3": 1, "mistv2": 2, "mist": 3}
            primary = min(entries, key=lambda e: _MODEL_RANK.get(e.get("modelId", ""), 99))

            gender = _GENDER_MAP.get((primary.get("gender") or "").lower())
            age = _parse_age(primary.get("age") or "")
            accent = _parse_accent(primary.get("country") or "", primary.get("lang") or "")
            use_cases = _parse_genre(primary.get("genre") or [])

            # Union all languages across all model entries for this speaker
            all_langs: list[str] = []
            seen_langs: set[str] = set()
            for entry in entries:
                bcp47 = _ISO3_TO_BCP47.get(entry.get("lang") or "")
                if bcp47 and bcp47 not in seen_langs:
                    seen_langs.add(bcp47)
                    all_langs.append(bcp47)
            languages = normalize_languages(all_langs)

            # Collect all model IDs in capability order
            model_ids = sorted(
                {e["modelId"] for e in entries if e.get("modelId")},
                key=lambda m: _MODEL_RANK.get(m, 99),
            )

            voices.append(SyncVoice(
                voice_id=speaker,
                display_name=speaker.replace("_", " ").title(),
                gender=gender,
                category="premade",
                languages=languages,
                accent=accent,
                age=age,
                use_cases=use_cases,
                compatible_models=model_ids,
                meta={
                    "country": primary.get("country"),
                    "dialect": primary.get("dialect"),
                    "demographic": primary.get("demographic"),
                    "flagship": primary.get("flagship", False),
                },
            ))
        return voices


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_age(age: str) -> str | None:
    if not age:
        return None
    return age.lower().replace(" ", "_")  # "Young Adult" → "young_adult"


def _parse_accent(country: str, lang: str) -> str | None:
    # Accent is only meaningful for English voices
    if lang != "eng":
        return None
    return _COUNTRY_TO_ACCENT.get(country.lower())


def _parse_genre(genre: list[str]) -> list[str]:
    """Flatten genre tags — entries may be comma-separated strings."""
    use_cases: list[str] = []
    for item in genre:
        for part in item.split(","):
            part = part.strip().lower()
            if part and part != "any":
                use_cases.append(part)
    return use_cases


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    syncer = RimeSyncer()
    try:
        result = await syncer.sync()
    except httpx.HTTPStatusError as e:
        print(f"\nRime API error ({e.response.status_code}): {e.response.text[:300]}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        print(
            f"  {m.model_id!r:12} {m.display_name!r:20} "
            f"langs={m.languages} is_default={m.is_default}"
        )

    # Show counts and a sample per model
    from collections import Counter
    model_counts = Counter(v.compatible_models[0] for v in result.tts_voices if v.compatible_models)
    print(f"\n=== TTS Voices ({len(result.tts_voices)}) — by model ===")
    for model_id, count in sorted(model_counts.items()):
        print(f"  {model_id!r:12} {count} voices")

    print(f"\n=== Sample voices (first 5 per model) ===")
    shown: dict[str, int] = {}
    for v in result.tts_voices:
        model = v.compatible_models[0] if v.compatible_models else "?"
        if shown.get(model, 0) >= 5:
            continue
        shown[model] = shown.get(model, 0) + 1
        print(
            f"  {v.voice_id!r:20} {v.display_name!r:22} "
            f"gender={v.gender or '?':6} accent={v.accent or '?':12} "
            f"age={v.age or '?':12} langs={v.languages}"
        )

    print(f"\nSource: {result.source}")
    print(f"Fetched at: {result.fetched_at}")


if __name__ == "__main__":
    asyncio.run(_main())
