"""
Google Cloud sync script.

Source: mixed
  - TTS voices : GET https://texttospeech.googleapis.com/v1/voices (API, single response)
  - TTS models : AI-parsed from https://docs.cloud.google.com/text-to-speech/docs/list-voices-and-types
  - STT models : AI-parsed from https://docs.cloud.google.com/speech-to-text/docs/transcription-model

Auth: X-goog-api-key header (API key restricted to Text-to-Speech API).

Google Cloud's "model" concept for TTS is the voice tier (Standard, Wavenet,
Neural2, Studio, Chirp-HD, Chirp3-HD). Tiers are encoded in each voice name
but not exposed as structured data — we AI-parse the docs page to get them
with display names and metadata. For voices whose tier isn't in the parsed
list (e.g., legacy Journey, Polyglot, Casual, News), we auto-inject the tier
as a minimal SyncModel so no voice ends up orphaned in the admin diff.

Voices fetch, TTS model parse, and STT model parse run concurrently.
"""

from __future__ import annotations

import asyncio

import httpx

from naaviq.config import settings
from naaviq.sync.ai_parser import AIParserError, parse_models_from_docs
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncModel, SyncResult, SyncVoice
from naaviq.sync.language import accent_from_languages, normalize_languages

_VOICES_URL = "https://texttospeech.googleapis.com/v1/voices"

_TTS_MODELS_DOCS_URLS = [
    "https://docs.cloud.google.com/text-to-speech/docs/list-voices-and-types",
]
_STT_MODELS_DOCS_URLS = [
    "https://docs.cloud.google.com/speech-to-text/docs/transcription-model",
]

_GENDER_MAP = {
    "MALE":    "male",
    "FEMALE":  "female",
    "NEUTRAL": "neutral",
    # SSML_VOICE_GENDER_UNSPECIFIED and other values → None
}

_TTS_MODELS_GUIDANCE = (
    "Google Cloud TTS organizes voices into tiers (Standard, Wavenet, Neural2, Studio, "
    "Chirp-HD, Chirp3-HD). Return EACH tier as a separate SyncModel. "
    "CRITICAL: the model_id MUST match how the tier appears in voice names exactly — "
    "use 'Wavenet' (not 'WaveNet' or 'wavenet'), 'Neural2' (not 'Neural 2'), "
    "'Chirp-HD' and 'Chirp3-HD' (hyphenated, no spaces). Case-exact. "
    "Mark the newest GA tier as is_default=true (currently 'Chirp3-HD'); only ONE model "
    "may have is_default=true. "
    "Set streaming=true for tiers documented as supporting real-time streaming, else false. "
    "If the docs don't enumerate specific languages for a tier, use ['*']. "
    "Populate `meta` with these keys: "
    "  is_ga          — true for GA tiers, false for experimental/preview"
    "  ssml_support   — true if the tier accepts SSML input, false otherwise"
    "  tier_class     — 'standard' for Standard, 'premium' for WaveNet/Neural2/Studio/Chirp variants"
    "  multispeaker   — true only for the Studio multispeaker variant"
)

_STT_MODELS_GUIDANCE = (
    "Google Cloud Speech-to-Text v2 exposes a small set of transcription models. Return ALL "
    "models listed on the docs page using their exact model_id strings (e.g., 'chirp_3', "
    "'chirp_2', 'telephony'). Mark the latest/recommended one as is_default=true (currently "
    "'chirp_3'); only ONE model may have is_default=true. "
    "Set streaming=true for models that support real-time streaming. "
    "If the docs say the model is multilingual without enumerating languages, use ['*']. "
    "Populate `meta` with capability flags mentioned in the docs: "
    "  diarization          — true if the model supports speaker diarization"
    "  auto_language_detect — true if the model can auto-detect the spoken language"
    "  specialization       — 'telephony' for 8kHz phone audio models, null for general models"
)


class GoogleCloudSyncer(ProviderSyncer):
    provider_id = "google-cloud"
    source = "mixed"

    async def sync(self) -> SyncResult:
        if not settings.google_cloud_api_key:
            raise ValueError("GOOGLE_CLOUD_API_KEY is not set in .env")

        ai_key = settings.anthropic_api_key or None

        voices_data, (tts_models, tts_notes), (stt_models, stt_notes) = await asyncio.gather(
            self._fetch_voices(),
            parse_models_from_docs(
                seed_urls=_TTS_MODELS_DOCS_URLS,
                provider_id=self.provider_id,
                model_type="tts",
                guidance=_TTS_MODELS_GUIDANCE,
                api_key=ai_key,
            ),
            parse_models_from_docs(
                seed_urls=_STT_MODELS_DOCS_URLS,
                provider_id=self.provider_id,
                model_type="stt",
                guidance=_STT_MODELS_GUIDANCE,
                api_key=ai_key,
            ),
        )

        tts_voices = self._parse_voices(voices_data)
        tts_models = self._inject_orphan_tiers(tts_models, tts_voices)

        from_cache = any(n.get("source") == "cache" for n in [tts_notes, stt_notes])
        if from_cache:
            notes = (
                f"Voices: {len(tts_voices)} from /v1/voices. "
                f"TTS models: {len(tts_models)} (cache). "
                f"STT models: {len(stt_models)} (cache)."
            )
        else:
            total_in  = tts_notes["input_tokens"]  + stt_notes["input_tokens"]
            total_out = tts_notes["output_tokens"] + stt_notes["output_tokens"]
            notes = (
                f"Voices: {len(tts_voices)} from /v1/voices. "
                f"TTS models: {len(tts_models)} from {len(tts_notes['urls_fetched'])} doc page(s). "
                f"STT models: {len(stt_models)} from {len(stt_notes['urls_fetched'])} doc page(s). "
                f"AI: {total_in} in / {total_out} out tokens ({tts_notes['model']})."
            )

        return SyncResult(
            stt_models=stt_models,
            tts_models=tts_models,
            tts_voices=tts_voices,
            source=self.source,
            api_urls=[_VOICES_URL],
            docs_urls=_TTS_MODELS_DOCS_URLS + _STT_MODELS_DOCS_URLS,
            notes=notes,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_voices(self) -> list[dict]:
        """Single-page call — Google returns all voices in one response (no pagination)."""
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(
                _VOICES_URL,
                headers={"X-goog-api-key": settings.google_cloud_api_key},
            )
            resp.raise_for_status()
            return resp.json().get("voices", [])

    def _parse_voices(self, items: list[dict]) -> list[SyncVoice]:
        voices: list[SyncVoice] = []
        for v in items:
            name = v.get("name")
            if not name:
                continue
            # Google's /v1/voices response includes 30 bare-character-name entries
            # ("Achernar", "Puck", …) that are verified 1:1 duplicates of the
            # canonical `en-US-Chirp3-HD-*` voices. Skip them — keeping both would
            # double-list every en-US Chirp3-HD voice in the registry.
            tier = _extract_tier(name)
            if tier is None:
                continue
            languages = normalize_languages(v.get("languageCodes") or [])
            voices.append(SyncVoice(
                voice_id=name,
                display_name=name,  # Google doesn't publish a separate display name
                gender=_GENDER_MAP.get(v.get("ssmlGender") or ""),
                category="premade",
                languages=languages,
                accent=accent_from_languages(languages),
                compatible_models=[tier],
                meta={
                    "sample_rate_hz": v.get("naturalSampleRateHertz"),
                },
            ))
        return voices

    def _inject_orphan_tiers(
        self,
        models: list[SyncModel],
        voices: list[SyncVoice],
    ) -> list[SyncModel]:
        """
        Add a minimal SyncModel for any tier referenced by voices but missing from the
        AI-parsed docs list. Keeps legacy tiers (Journey, Polyglot, Casual, News) visible
        in the admin diff instead of silently dropping their voices.
        """
        known = {m.model_id for m in models}
        orphans: dict[str, set[str]] = {}
        for v in voices:
            for tier in v.compatible_models:
                if tier not in known:
                    orphans.setdefault(tier, set()).update(v.languages)

        for tier, langs in orphans.items():
            models.append(SyncModel(
                model_id=tier,
                display_name=tier,
                type="tts",
                languages=sorted(langs),
                streaming=False,
                is_default=False,
                description="Legacy tier inferred from voice names (not listed in current tier docs).",
                meta={"is_ga": False, "orphan": True},
            ))
        return models


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_tier(voice_name: str) -> str | None:
    """
    Extract the tier segment from a Google voice name.

    Examples:
        "en-US-Standard-A"        → "Standard"
        "en-US-Wavenet-A"         → "Wavenet"
        "en-US-Neural2-A"         → "Neural2"
        "en-US-Studio-Q"          → "Studio"
        "en-US-Journey-D"         → "Journey"
        "en-US-Chirp-HD-D"        → "Chirp-HD"
        "en-US-Chirp3-HD-Aoede"   → "Chirp3-HD"
        "cmn-CN-Wavenet-A"        → "Wavenet"
    """
    parts = voice_name.split("-")
    if len(parts) < 4:
        return None
    # parts[0:2] is the locale (e.g., en-US, cmn-CN); parts[2] is the tier root.
    # If parts[3] is "HD", fold it into the tier (Chirp-HD, Chirp3-HD).
    if len(parts) >= 5 and parts[3] == "HD":
        return f"{parts[2]}-HD"
    return parts[2]


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    syncer = GoogleCloudSyncer()
    try:
        result = await syncer.sync()
    except (ValueError, AIParserError, RuntimeError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        raise SystemExit(1)
    except httpx.HTTPStatusError as e:
        print(
            f"\nGoogle Cloud API error ({e.response.status_code}): {e.response.text[:300]}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print(f"\n=== STT Models ({len(result.stt_models)}) ===")
    for m in result.stt_models:
        marker = " [default]" if m.is_default else ""
        print(f"  {m.model_id!r:25} {m.display_name!r:30} langs={len(m.languages)}{marker}")
        if m.meta:
            print(f"    meta: {m.meta}")

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        marker = " [default]" if m.is_default else ""
        orphan = " (orphan)" if m.meta.get("orphan") else ""
        print(f"  {m.model_id!r:25} {m.display_name!r:30} langs={len(m.languages)}{marker}{orphan}")
        if m.meta:
            print(f"    meta: {m.meta}")

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) — showing first 20 ===")
    for v in result.tts_voices[:20]:
        print(
            f"  {v.voice_id!r:40} models={v.compatible_models!r:15} "
            f"gender={v.gender} accent={v.accent} lang={v.languages}"
        )
    if len(result.tts_voices) > 20:
        print(f"  ... and {len(result.tts_voices) - 20} more")

    print(f"\nSource: {result.source}")
    print(f"Notes:  {result.notes}")


if __name__ == "__main__":
    asyncio.run(_main())
