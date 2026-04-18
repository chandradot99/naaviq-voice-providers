"""
Hume AI sync script.

Source: mixed
  - TTS voices: GET https://api.hume.ai/v0/tts/voices?provider=HUME_AI (API, paginated)
  - TTS models: AI-parsed from docs (no /models endpoint)
  - STT: not offered — stt_models=[]

Voice API response shape:
  {
    "page_number": 0,
    "page_size": 100,
    "total_pages": 2,
    "voices_page": [
      {
        "id": "<uuid>",
        "name": "Colton Rivers",
        "provider": "HUME_AI",
        "tags": {
          "LANGUAGE": ["English"],
          "ACCENT":   ["American", "Southern"],
          "GENDER":   ["Male"],
          "AGE":      ["Middle-Aged"]
        },
        "compatible_octave_models": ["1", "2"]
      },
      ...
    ]
  }

Auth: X-Hume-Api-Key header, HUME_API_KEY env var.
"""

from __future__ import annotations

import asyncio

import httpx

from naaviq.config import settings
from naaviq.sync.ai_parser import parse_models_from_docs
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncResult, SyncVoice

_VOICES_URL = "https://api.hume.ai/v0/tts/voices"
_PAGE_SIZE = 100

_DOCS_SEED_URLS = ["https://dev.hume.ai/docs/text-to-speech-tts/overview"]
_MODEL_GUIDANCE = (
    "Extract Hume AI Octave TTS models. "
    "There are currently two: octave-2 (latest, multilingual — en, ja, ko, es, fr, pt, it, de, ru, hi, ar) "
    "and octave-1 (legacy, English and Spanish only). "
    "Mark octave-2 as is_default=True. Both models support streaming. "
    "Set the model_id exactly as shown: 'octave-2' and 'octave-1'."
)

# Hume returns human-readable language names in tags — map to BCP-47
_LANGUAGE_NAME_TO_BCP47: dict[str, str] = {
    "english":    "en",
    "spanish":    "es",
    "japanese":   "ja",
    "korean":     "ko",
    "french":     "fr",
    "portuguese": "pt",
    "italian":    "it",
    "german":     "de",
    "russian":    "ru",
    "hindi":      "hi",
    "arabic":     "ar",
    "chinese":    "zh",
    "dutch":      "nl",
    "polish":     "pl",
    "swedish":    "sv",
    "turkish":    "tr",
}

# Hume accent labels → our accent format
_ACCENT_MAP: dict[str, str] = {
    "american":           "american",
    "british":            "british",
    "australian":         "australian",
    "indian":             "indian",
    "canadian":           "canadian",
    "irish":              "irish",
    "south african":      "south_african",
    "new zealand":        "new_zealander",
    "black american":     "american",
    "received pronunciation": "british",
    "english":            "british",
}

# Octave model number → our model_id
_OCTAVE_NUM_TO_MODEL: dict[str, str] = {
    "1": "octave-1",
    "2": "octave-2",
}


class HumeAISyncer(ProviderSyncer):
    provider_id = "humeai"
    source = "mixed"

    async def sync(self) -> SyncResult:
        voices_data, (tts_models, notes) = await asyncio.gather(
            self._fetch_all_voices(),
            parse_models_from_docs(
                seed_urls=_DOCS_SEED_URLS,
                provider_id=self.provider_id,
                model_type="tts",
                guidance=_MODEL_GUIDANCE,
            ),
        )
        return SyncResult(
            stt_models=[],
            tts_models=tts_models,
            tts_voices=self._parse_voices(voices_data),
            source=self.source,
            api_urls=[_VOICES_URL],
            docs_urls=_DOCS_SEED_URLS,
            notes=notes,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_all_voices(self) -> list[dict]:
        if not settings.hume_api_key:
            raise ValueError("HUME_API_KEY is not set in .env")

        headers = {"X-Hume-Api-Key": settings.hume_api_key}
        all_voices: list[dict] = []

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            # Fetch page 0 first to learn total_pages
            resp = await client.get(
                _VOICES_URL,
                headers=headers,
                params={"provider": "HUME_AI", "page_number": 0, "page_size": _PAGE_SIZE},
            )
            resp.raise_for_status()
            body = resp.json()
            all_voices.extend(body.get("voices_page", []))

            total_pages: int = body.get("total_pages", 1)

            # Fetch remaining pages concurrently
            if total_pages > 1:
                tasks = [
                    client.get(
                        _VOICES_URL,
                        headers=headers,
                        params={"provider": "HUME_AI", "page_number": p, "page_size": _PAGE_SIZE},
                    )
                    for p in range(1, total_pages)
                ]
                responses = await asyncio.gather(*tasks)
                for r in responses:
                    r.raise_for_status()
                    all_voices.extend(r.json().get("voices_page", []))

        return all_voices

    def _parse_voices(self, voices_data: list[dict]) -> list[SyncVoice]:
        voices: list[SyncVoice] = []
        for v in voices_data:
            voice_id = v.get("id")
            if not voice_id:
                continue

            tags: dict[str, list[str]] = v.get("tags") or {}

            gender = _parse_gender(tags.get("GENDER") or [])
            accent = _parse_accent(tags.get("ACCENT") or [])
            age = _parse_age(tags.get("AGE") or [])
            languages = _parse_languages(tags.get("LANGUAGE") or [])

            octave_nums = v.get("compatible_octave_models") or []
            compatible_models = [
                _OCTAVE_NUM_TO_MODEL[n]
                for n in octave_nums
                if n in _OCTAVE_NUM_TO_MODEL
            ]

            voices.append(SyncVoice(
                voice_id=voice_id,
                display_name=v.get("name", voice_id),
                gender=gender,
                category="premade",
                languages=languages,
                accent=accent,
                age=age,
                compatible_models=compatible_models,
                meta={"raw_accents": tags.get("ACCENT")},
            ))
        return voices


# ── Tag parsers ───────────────────────────────────────────────────────────────

def _parse_gender(values: list[str]) -> str | None:
    if not values:
        return None
    g = values[0].lower()
    if g in ("male", "female", "neutral"):
        return g
    return None


def _parse_accent(values: list[str]) -> str | None:
    for v in values:
        mapped = _ACCENT_MAP.get(v.lower())
        if mapped:
            return mapped
    return None


def _parse_age(values: list[str]) -> str | None:
    return values[0].lower().replace("-", "_") if values else None


def _parse_languages(values: list[str]) -> list[str]:
    result: list[str] = []
    for v in values:
        bcp47 = _LANGUAGE_NAME_TO_BCP47.get(v.lower())
        if bcp47:
            result.append(bcp47)
    return result or ["*"]


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    syncer = HumeAISyncer()
    try:
        result = await syncer.sync()
    except ValueError as e:
        print(f"\nError: {e}", file=sys.stderr)
        raise SystemExit(1)
    except httpx.HTTPStatusError as e:
        print(f"\nHume AI API error ({e.response.status_code}): {e.response.text[:300]}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        print(
            f"  {m.model_id!r:15} {m.display_name!r:20} "
            f"langs={m.languages} is_default={m.is_default}"
        )

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) — showing first 20 ===")
    for v in result.tts_voices[:20]:
        print(
            f"  {v.voice_id!r:40} {v.display_name!r:30} "
            f"gender={v.gender or '?':6} accent={v.accent or '?':12} "
            f"age={v.age or '?':12} langs={v.languages} models={v.compatible_models}"
        )
    if len(result.tts_voices) > 20:
        print(f"  ... and {len(result.tts_voices) - 20} more")

    print(f"\nSource: {result.source}")
    print(f"Fetched at: {result.fetched_at}")
    if result.notes:
        print(f"\nNotes: {result.notes}")


if __name__ == "__main__":
    asyncio.run(_main())
