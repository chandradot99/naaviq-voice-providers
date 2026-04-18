"""
Murf AI sync script.

Source: api
  - TTS voices: GET https://api.murf.ai/v1/speech/voices
  - TTS models: 2 derived constants (Falcon + Gen2) — no /models endpoint
  - STT: not offered — stt_models=[]

Voice API response fields:
  voiceId           → voice_id
  displayName       → display_name
  gender            → gender ("Male" → "male", "NonBinary" → "neutral")
  locale            → primary language
  supportedLocales  → additional languages + styles per locale
  description       → meta

Auth: api-key header, MURF_API_KEY env var.
"""

from __future__ import annotations

import httpx

from naaviq.config import settings
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncModel, SyncResult, SyncVoice
from naaviq.sync.language import ACCENT_MAP, normalize_languages

_VOICES_URL = "https://api.murf.ai/v1/speech/voices"

_GENDER_MAP: dict[str, str] = {
    "male":      "male",
    "female":    "female",
    "nonbinary": "neutral",
}


class MurfAISyncer(ProviderSyncer):
    provider_id = "murf"
    source = "api"

    async def sync(self) -> SyncResult:
        voices_data = await self._fetch_voices()
        tts_voices = self._parse_voices(voices_data)
        tts_models = self._derive_tts_models(voices_data)
        return SyncResult(
            stt_models=[],
            tts_models=tts_models,
            tts_voices=tts_voices,
            source=self.source,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_voices(self) -> list[dict]:
        if not settings.murf_api_key:
            raise ValueError("MURF_API_KEY is not set in .env")

        headers = {"api-key": settings.murf_api_key}

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(_VOICES_URL, headers=headers)
            resp.raise_for_status()
            body = resp.json()

        if isinstance(body, list):
            return body
        return body.get("voices", body.get("items", []))

    def _parse_voices(self, voices_data: list[dict]) -> list[SyncVoice]:
        voices: list[SyncVoice] = []
        for v in voices_data:
            voice_id = v.get("voiceId")
            if not voice_id:
                continue

            gender = _parse_gender(v.get("gender"))
            locale = v.get("locale") or ""
            supported_locales = v.get("supportedLocales") or {}

            all_locales = [locale] if locale else []
            all_locales.extend(supported_locales.keys())
            languages = sorted(set(normalize_languages([loc for loc in all_locales if loc])))

            accent = _accent_from_locale(locale)

            meta: dict = {}
            if v.get("description"):
                meta["description"] = v["description"]
            if supported_locales:
                meta["supported_locales"] = supported_locales

            voices.append(SyncVoice(
                voice_id=voice_id,
                display_name=v.get("displayName") or voice_id,
                gender=gender,
                category="premade",
                languages=languages,
                accent=accent,
                compatible_models=[],
                meta=meta,
            ))
        return voices

    def _derive_tts_models(self, voices_data: list[dict]) -> list[SyncModel]:
        all_langs: set[str] = set()
        for v in voices_data:
            locale = v.get("locale")
            if locale:
                all_langs.update(normalize_languages([locale]))
            for loc in (v.get("supportedLocales") or {}):
                all_langs.update(normalize_languages([loc]))

        langs = sorted(all_langs)

        return [
            SyncModel(
                model_id="falcon",
                display_name="Murf Falcon",
                type="tts",
                languages=langs,
                streaming=True,
                is_default=True,
                meta={"latency_ms": 55, "ttfa_ms": 130},
            ),
            SyncModel(
                model_id="gen2",
                display_name="Murf Gen2",
                type="tts",
                languages=langs,
                streaming=True,
                is_default=False,
                meta={"supports_duration": True, "supports_style": True},
            ),
        ]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_gender(value: str | None) -> str | None:
    if not value:
        return None
    return _GENDER_MAP.get(value.lower())


_MURF_REGION_TO_ACCENT: dict[str, str] = {
    "UK": "british",
    "Scott": "scottish",
}


def _accent_from_locale(locale: str) -> str | None:
    parts = locale.split("-")
    if len(parts) >= 2:
        region = parts[1]
        return _MURF_REGION_TO_ACCENT.get(region) or ACCENT_MAP.get(region.upper())
    return None


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import asyncio
    import sys

    syncer = MurfAISyncer()
    try:
        result = await syncer.sync()
    except ValueError as e:
        print(f"\nError: {e}", file=sys.stderr)
        raise SystemExit(1)
    except httpx.HTTPStatusError as e:
        print(f"\nMurf API error ({e.response.status_code}): {e.response.text[:300]}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        print(
            f"  {m.model_id!r:15} {m.display_name!r:20} "
            f"langs={len(m.languages)} is_default={m.is_default}"
        )

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) — showing first 20 ===")
    for v in result.tts_voices[:20]:
        print(
            f"  {v.voice_id!r:25} {v.display_name!r:20} "
            f"gender={v.gender or '?':6} accent={v.accent or '?':12} "
            f"langs={v.languages}"
        )
    if len(result.tts_voices) > 20:
        print(f"  ... and {len(result.tts_voices) - 20} more")

    print(f"\nSource: {result.source}")
    print(f"Fetched at: {result.fetched_at}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(_main())
