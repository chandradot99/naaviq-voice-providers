"""
Azure Speech sync script.

Source: GET https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list (API)
  - All TTS voices for the region (global catalog — any region returns the same set)
  - TTS models derived from unique VoiceType values in the voice list
  - STT models: two synthetic entries (realtime + batch) — Azure STT is locale-based, no named models

Voice API fields used:
  ShortName    → voice_id  (e.g., "en-US-JennyNeural")
  DisplayName  → display_name
  Gender       → gender
  Locale       → primary language
  SecondaryLocaleList → additional languages for multilingual voices
  StyleList    → speaking styles (in meta)
  RolePlayList → character roles (in meta)
  VoiceType    → derives TTS model (Neural | Standard)
  Status       → skip non-GA voices (configurable)
  WordsPerMinute → in meta
  SampleRateHertz → in meta
  ExtendedPropertyMap.IsHighQuality48K → in meta
"""

from __future__ import annotations

import httpx

from naaviq.config import settings
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncModel, SyncResult, SyncVoice
from naaviq.sync.language import ACCENT_MAP, normalize_languages

_VOICES_URL = "https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list"

# VoiceType → model_id
_VOICE_TYPE_MODEL: dict[str, str] = {
    "Neural":   "azure-neural",
    "Standard": "azure-standard",
}

# VoiceType → display_name
_VOICE_TYPE_DISPLAY: dict[str, str] = {
    "Neural":   "Azure Neural",
    "Standard": "Azure Standard",
}


class AzureSyncer(ProviderSyncer):
    provider_id = "azure"
    source = "api"

    async def sync(self) -> SyncResult:
        voices_data = await self._fetch_voices()
        tts_voices = self._parse_voices(voices_data)
        tts_models = self._derive_tts_models(voices_data)
        stt_models = self._synthetic_stt_models()
        return SyncResult(
            stt_models=stt_models,
            tts_models=tts_models,
            tts_voices=tts_voices,
            source=self.source,
            api_urls=[_VOICES_URL],
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_voices(self) -> list[dict]:
        if not settings.azure_speech_key:
            raise ValueError("AZURE_SPEECH_KEY is not set in .env")
        region = settings.azure_speech_region or "eastus"
        url = _VOICES_URL.format(region=region)
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(
                url,
                headers={"Ocp-Apim-Subscription-Key": settings.azure_speech_key},
            )
            resp.raise_for_status()
            return resp.json()

    def _parse_voices(self, voices_data: list[dict]) -> list[SyncVoice]:
        voices: list[SyncVoice] = []

        for v in voices_data:
            # Skip non-GA voices — preview and deprecated voices add noise
            if v.get("Status", "GA") != "GA":
                continue

            voice_type = v.get("VoiceType", "Neural")
            model_id = _VOICE_TYPE_MODEL.get(voice_type)

            # Primary locale + secondary locales for multilingual voices
            primary_locale = v.get("Locale", "")
            secondary_locales = v.get("SecondaryLocaleList") or []
            all_locales = [primary_locale] + secondary_locales
            languages = normalize_languages([loc for loc in all_locales if loc])

            # Derive accent from the primary locale region
            accent = _accent_from_locale(primary_locale)

            extended = v.get("ExtendedPropertyMap") or {}

            voices.append(SyncVoice(
                voice_id=v["ShortName"],
                display_name=v.get("DisplayName", v["ShortName"]),
                gender=v.get("Gender", "").lower() or None,
                category="premade",
                languages=languages,
                accent=accent,
                compatible_models=[model_id] if model_id else [],
                meta={
                    "voice_type":         voice_type,
                    "locale_name":        v.get("LocaleName"),
                    "style_list":         v.get("StyleList") or [],
                    "role_play_list":     v.get("RolePlayList") or [],
                    "words_per_minute":   v.get("WordsPerMinute"),
                    "sample_rate_hertz":  v.get("SampleRateHertz"),
                    "is_high_quality_48k": extended.get("IsHighQuality48K") == "True",
                },
            ))

        return voices

    def _derive_tts_models(self, voices_data: list[dict]) -> list[SyncModel]:
        """Build one TTS model entry per unique VoiceType found in the voice list."""
        type_langs: dict[str, set[str]] = {}

        for v in voices_data:
            if v.get("Status", "GA") != "GA":
                continue
            voice_type = v.get("VoiceType", "Neural")
            if voice_type not in _VOICE_TYPE_MODEL:
                continue
            if voice_type not in type_langs:
                type_langs[voice_type] = set()
            locale = v.get("Locale", "")
            if locale:
                type_langs[voice_type].update(normalize_languages([locale]))

        models: list[SyncModel] = []
        for voice_type, langs in type_langs.items():
            model_id = _VOICE_TYPE_MODEL[voice_type]
            models.append(SyncModel(
                model_id=model_id,
                display_name=_VOICE_TYPE_DISPLAY[voice_type],
                type="tts",
                languages=sorted(langs),
                streaming=True,
                is_default=(voice_type == "Neural"),  # Neural is the current default
                meta={"voice_type": voice_type},
            ))

        # Neural first
        models.sort(key=lambda m: (m.model_id != "azure-neural",))
        return models

    def _synthetic_stt_models(self) -> list[SyncModel]:
        """
        Azure STT has no named models — it's locale-based with distinct operational modes.
        Two synthetic entries give users a meaningful choice that maps to the Azure API docs.
        """
        return [
            SyncModel(
                model_id="azure-stt-realtime",
                display_name="Azure STT Realtime",
                type="stt",
                languages=["*"],
                streaming=True,
                is_default=True,
                description="Azure Speech-to-Text real-time recognition. Locale-based; streaming supported.",
                meta={"mode": "realtime"},
            ),
            SyncModel(
                model_id="azure-stt-batch",
                display_name="Azure STT Batch",
                type="stt",
                languages=["*"],
                streaming=False,
                is_default=False,
                description="Azure Speech-to-Text batch transcription. Locale-based; async processing.",
                meta={"mode": "batch"},
            ),
        ]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _accent_from_locale(locale: str) -> str | None:
    """Derive accent from a BCP-47 locale. e.g., 'en-GB' → 'british'."""
    parts = locale.split("-")
    if len(parts) >= 2:
        region = parts[1].upper()
        # Azure uses 4-letter script subtags in some locales (e.g., zh-Hans-CN)
        # In that case parts[1] is the script, parts[2] is the region
        if len(parts) >= 3 and len(parts[1]) == 4:
            region = parts[2].upper()
        return ACCENT_MAP.get(region)
    return None


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    syncer = AzureSyncer()
    try:
        result = await syncer.sync()
    except ValueError as e:
        print(f"\nError: {e}", file=sys.stderr)
        raise SystemExit(1)
    except httpx.HTTPStatusError as e:
        print(f"\nAzure API error ({e.response.status_code}): {e.response.text[:300]}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== STT Models ({len(result.stt_models)}) ===")
    for m in result.stt_models:
        print(f"  {m.model_id!r:30} {m.display_name!r:25} streaming={m.streaming} is_default={m.is_default}")
        if m.description:
            print(f"    {m.description}")

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        print(f"  {m.model_id!r:20} {m.display_name!r:20} langs={len(m.languages)} is_default={m.is_default}")

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) — showing first 20 ===")
    for v in result.tts_voices[:20]:
        styles = len(v.meta.get("style_list") or [])
        print(
            f"  {v.voice_id!r:30} {v.display_name!r:18} "
            f"gender={v.gender or '?':6} accent={v.accent or '':12} "
            f"styles={styles} models={v.compatible_models}"
        )
    if len(result.tts_voices) > 20:
        print(f"  ... and {len(result.tts_voices) - 20} more")

    print(f"\nSource: {result.source}")
    print(f"Fetched at: {result.fetched_at}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(_main())
