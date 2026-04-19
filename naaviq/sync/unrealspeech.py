"""
Unreal Speech sync script.

Source: docs
  - TTS model  : AI-parsed from docs (one model: kokoro)
  - TTS voices : AI-parsed from docs (48 voices, 8 languages)
  - STT        : not offered — stt_models=[]

No voice listing REST endpoint exists; voices are documented on the studio page.
Auth: Authorization: Bearer API_KEY
Config: UNREAL_SPEECH_API_KEY (reserved; all data comes from public docs)
"""

from __future__ import annotations

import asyncio
import sys

from naaviq.sync.ai_parser import AIParserError, parse_models_from_docs, parse_voices_from_docs
from naaviq.sync.base import ProviderSyncer, SyncResult

_DOCS_URLS = [
    "https://docs.unrealspeech.com/",
    "https://docs.v8.unrealspeech.com/",
]

_TTS_MODEL_GUIDANCE = """
Extract Unreal Speech TTS models. There is 1 model.

1. model_id="kokoro", display_name="Kokoro", is_default=True, streaming=True
   - Current engine powering Unreal Speech v8.
   - 8 languages: US English, UK English, French, Hindi, Spanish, Portuguese, Japanese, Mandarin.
   - description="Unreal Speech Kokoro TTS — 8 languages, streaming, low latency."
   - languages: ["en", "en-GB", "fr", "hi", "es", "pt", "ja", "zh"]

Use exact model_id: "kokoro".
"""

_VOICES_GUIDANCE = """
Return every built-in TTS voice for Unreal Speech (Kokoro engine). There are ~48 voices.

voice_id = the exact API voice ID string (e.g., "Hannah", "af_bella", "Scarlett").
display_name = human-readable name.
languages = language(s) the voice supports as BCP-47 codes.
gender = "male" | "female" | null.
compatible_models = ["kokoro"] for all voices.
category = "premade".

Key voices include:
- American English female: Hannah, Kaitlyn, Lauren, Sierra, af_alloy, af_aoede, af_bella, af_heart, af_jessica, af_kore, af_nicole, af_nova, af_river, af_sarah, af_sky
- American English male: Noah, Daniel, am_adam, am_echo, am_eric, am_fenrir, am_liam, am_michael, am_onyx, am_puck
- British English female: Chloe, Amelia, bf_alice, bf_emma, bf_isabella, bf_lily
- British English male: Edward, Oliver, bm_daniel, bm_fable, bm_george, bm_lewis
- French female: Élodie (voice_id="Elodie")
- Hindi female: Ananya, Priya
- Spanish female: Lucía (voice_id="Lucia"), Spanish male: Mateo
- Portuguese female: Camila, Portuguese male: Thiago
- Japanese female: Sakura, Hana
- Italian female: Giulia, Italian male: Luca
- Chinese/Mandarin female: Mei, Lian
- Legacy voices (still supported): Scarlett (female, en), Dan (male, en), Liv (female, en), Will (male, en), Amy (female, en)

For legacy voices (Scarlett, Dan, Liv, Will, Amy) set compatible_models=["kokoro"].
"""


class UnrealSpeechSyncer(ProviderSyncer):
    provider_id = "unrealspeech"
    source = "docs"

    async def sync(self) -> SyncResult:
        (tts_models, tts_notes), (tts_voices, voice_notes) = await asyncio.gather(
            parse_models_from_docs(
                seed_urls=_DOCS_URLS,
                provider_id=self.provider_id,
                model_type="tts",
                guidance=_TTS_MODEL_GUIDANCE,
            ),
            parse_voices_from_docs(
                seed_urls=_DOCS_URLS,
                provider_id=self.provider_id,
                guidance=_VOICES_GUIDANCE,
            ),
        )

        from_cache = (
            isinstance(tts_notes, dict) and tts_notes.get("source") == "cache"
            and isinstance(voice_notes, dict) and voice_notes.get("source") == "cache"
        )
        notes = (
            f"{len(tts_models)} TTS models (cache). {len(tts_voices)} voices (cache)."
            if from_cache else
            f"{len(tts_models)} TTS models. {len(tts_voices)} voices."
        )

        return SyncResult(
            stt_models=[],
            tts_models=tts_models,
            tts_voices=tts_voices,
            source=self.source,
            docs_urls=_DOCS_URLS,
            notes=notes,
        )


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    syncer = UnrealSpeechSyncer()
    try:
        result = await syncer.sync()
    except AIParserError as e:
        print(f"\nError: {e}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        marker = " [default]" if m.is_default else ""
        print(f"  {m.model_id!r:12} {m.display_name!r:20} langs={m.languages}{marker}")

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) ===")
    for v in result.tts_voices:
        print(
            f"  {v.voice_id!r:20} {v.display_name!r:22} "
            f"gender={v.gender or '?':6} langs={v.languages}"
        )

    print(f"\nSource : {result.source}")
    print(f"Notes  : {result.notes}")
    print(f"Fetched: {result.fetched_at}")


if __name__ == "__main__":
    asyncio.run(_main())
