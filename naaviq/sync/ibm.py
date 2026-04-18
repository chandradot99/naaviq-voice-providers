"""
IBM Watson sync script.

Source: api
  - STT models: GET /v1/models (next-generation Multimedia + Telephony only)
  - TTS voices: GET /v1/voices (Natural, Expressive, Enhanced Neural tiers)
  - TTS models: derived from voice tiers (no separate /models endpoint)

IBM Watson uses regional API endpoints. Configure IBM_TTS_URL and IBM_STT_URL
to point to your instance region (default: us-south).

Auth: HTTP Basic with username "apikey" and the API key as password.
  Authorization: Basic base64("apikey:{IBM_TTS_API_KEY}") for TTS
  Authorization: Basic base64("apikey:{IBM_STT_API_KEY}") for STT

STT model naming convention:
  Next-gen (kept): {lang}_Multimedia (16kHz), {lang}_Telephony (8kHz)
  Previous-gen (skipped): {lang}_BroadbandModel, {lang}_NarrowbandModel

TTS voice tiers (derived as models):
  ibm-natural     — Natural voices (*Natural suffix)
  ibm-expressive  — Expressive Neural voices (*Expressive suffix)
  ibm-neural      — Enhanced Neural voices (*V3Voice suffix)
"""

from __future__ import annotations

import asyncio
import base64

import httpx

from naaviq.config import settings
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncModel, SyncResult, SyncVoice
from naaviq.sync.language import normalize_languages

_TTS_VOICES_PATH = "/v1/voices"
_STT_MODELS_PATH = "/v1/models"

_API_DOCS_URLS = [
    "https://cloud.ibm.com/apidocs/text-to-speech",
    "https://cloud.ibm.com/apidocs/speech-to-text",
]

# Voice tier → (model_id, display_name, is_default)
_VOICE_TIERS: dict[str, tuple[str, str, bool]] = {
    "natural":    ("ibm-natural",    "IBM Natural",          True),
    "expressive": ("ibm-expressive", "IBM Expressive",       False),
    "neural":     ("ibm-neural",     "IBM Enhanced Neural",  False),
}

_GENDER_MAP = {"male": "male", "female": "female"}


class IBMSyncer(ProviderSyncer):
    provider_id = "ibm"
    source = "api"

    async def sync(self) -> SyncResult:
        tts_key = settings.ibm_tts_api_key
        stt_key = settings.ibm_stt_api_key
        if not tts_key:
            raise ValueError("IBM_TTS_API_KEY is not set in .env")
        if not stt_key:
            raise ValueError("IBM_STT_API_KEY is not set in .env")

        tts_url = settings.ibm_tts_url.rstrip("/")
        stt_url = settings.ibm_stt_url.rstrip("/")

        voices_raw, models_raw = await asyncio.gather(
            self._fetch(tts_url + _TTS_VOICES_PATH, tts_key),
            self._fetch(stt_url + _STT_MODELS_PATH, stt_key),
        )

        tts_voices = self._parse_voices(voices_raw.get("voices") or [])
        tts_models = self._derive_tts_models(voices_raw.get("voices") or [])
        stt_models = self._parse_stt_models(models_raw.get("models") or [])

        return SyncResult(
            stt_models=stt_models,
            tts_models=tts_models,
            tts_voices=tts_voices,
            source=self.source,
            api_urls=[
                tts_url + _TTS_VOICES_PATH,
                stt_url + _STT_MODELS_PATH,
            ],
            notes=(
                f"TTS: {len(tts_voices)} voices, {len(tts_models)} model tiers. "
                f"STT: {len(stt_models)} next-gen models."
            ),
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch(self, url: str, api_key: str) -> dict:
        token = base64.b64encode(f"apikey:{api_key}".encode()).decode()
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(url, headers={"Authorization": f"Basic {token}"})
            resp.raise_for_status()
            return resp.json()

    def _parse_voices(self, voices: list[dict]) -> list[SyncVoice]:
        result = []
        for v in voices:
            name: str = v.get("name", "")
            tier = _voice_tier(name)
            if tier == "standard":
                continue  # skip deprecated standard voices

            lang = v.get("language", "")
            model_id = _VOICE_TIERS[tier][0]

            result.append(SyncVoice(
                voice_id=name,
                display_name=_voice_display_name(name),
                gender=_GENDER_MAP.get(v.get("gender", "").lower()),
                category="premade",
                languages=normalize_languages([lang]) if lang else [],
                description=v.get("description"),
                compatible_models=[model_id],
            ))
        return result

    def _derive_tts_models(self, voices: list[dict]) -> list[SyncModel]:
        tiers_present: set[str] = set()
        tier_langs: dict[str, set[str]] = {}
        for v in voices:
            name: str = v.get("name", "")
            tier = _voice_tier(name)
            if tier == "standard":
                continue
            tiers_present.add(tier)
            lang = v.get("language", "")
            if lang:
                tier_langs.setdefault(tier, set()).add(lang)

        models = []
        for tier_key, (model_id, display_name, is_default) in _VOICE_TIERS.items():
            if tier_key not in tiers_present:
                continue
            langs = normalize_languages(sorted(tier_langs.get(tier_key, [])))
            models.append(SyncModel(
                model_id=model_id,
                display_name=display_name,
                type="tts",
                languages=langs,
                streaming=True,
                is_default=is_default,
            ))
        return models

    def _parse_stt_models(self, models: list[dict]) -> list[SyncModel]:
        result = []
        for m in models:
            name: str = m.get("name", "")
            # Keep only next-generation models (Multimedia / Telephony)
            if not (_is_multimedia(name) or _is_telephony(name)):
                continue

            lang = m.get("language", "")
            is_default = name == "en-US_Multimedia"

            result.append(SyncModel(
                model_id=name,
                display_name=_stt_display_name(name),
                type="stt",
                languages=normalize_languages([lang]) if lang else [],
                streaming=True,
                is_default=is_default,
                description=m.get("description"),
                meta={"rate": m.get("rate"), "low_latency": True},
            ))
        return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _voice_tier(name: str) -> str:
    if name.endswith("Natural"):
        return "natural"
    if name.endswith("Expressive"):
        return "expressive"
    if "V3Voice" in name:
        return "neural"
    return "standard"


def _is_multimedia(name: str) -> bool:
    return name.endswith("_Multimedia")


def _is_telephony(name: str) -> bool:
    return "_Telephony" in name


def _voice_display_name(name: str) -> str:
    # "en-US_AllisonExpressive" → "Allison"
    # "en-US_AllisonV3Voice"    → "Allison"
    # "en-US_EllieNatural"      → "Ellie"
    after_lang = name.split("_", 1)[-1] if "_" in name else name
    for suffix in ("Expressive", "Natural", "V3Voice"):
        if after_lang.endswith(suffix):
            return after_lang[: -len(suffix)]
    return after_lang


def _stt_display_name(name: str) -> str:
    # "en-US_Multimedia" → "English (US) Multimedia"
    # "en-WW_Medical_Telephony" → "English (WW) Medical Telephony"
    parts = name.split("_", 1)
    lang_code = parts[0]
    rest = parts[1].replace("_", " ") if len(parts) > 1 else ""
    lang_map = {
        "ar-MS": "Arabic (Modern Standard)", "zh-CN": "Chinese (Mandarin)",
        "cs-CZ": "Czech", "nl-BE": "Dutch (Belgium)", "nl-NL": "Dutch (Netherlands)",
        "en-AU": "English (Australia)", "en-IN": "English (India)",
        "en-GB": "English (UK)", "en-US": "English (US)", "en-WW": "English (Worldwide)",
        "fr-CA": "French (Canada)", "fr-FR": "French (France)",
        "de-DE": "German", "hi-IN": "Hindi", "it-IT": "Italian",
        "ja-JP": "Japanese", "ko-KR": "Korean", "pt-BR": "Portuguese (Brazil)",
        "es-ES": "Spanish (Spain)", "es-LA": "Spanish (Latin America)",
        "sv-SE": "Swedish",
    }
    lang_label = lang_map.get(lang_code, lang_code)
    return f"{lang_label} {rest}".strip()


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    syncer = IBMSyncer()
    try:
        result = await syncer.sync()
    except (ValueError, httpx.HTTPStatusError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== STT Models ({len(result.stt_models)}) ===")
    for m in result.stt_models:
        marker = " [default]" if m.is_default else ""
        print(f"  {m.model_id!r:35} {m.languages}{marker}")

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        marker = " [default]" if m.is_default else ""
        print(f"  {m.model_id!r:20} {m.display_name!r:25} langs={len(m.languages)}{marker}")

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) ===")
    for v in result.tts_voices:
        print(
            f"  {v.voice_id!r:35} {v.display_name!r:15} "
            f"lang={v.languages} gender={v.gender} model={v.compatible_models}"
        )

    print(f"\nSource : {result.source}")
    print(f"Notes  : {result.notes}")
    print(f"Fetched: {result.fetched_at}")


if __name__ == "__main__":
    asyncio.run(_main())
