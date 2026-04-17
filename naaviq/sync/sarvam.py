"""
Sarvam sync script.

Source: docs
  - STT models : AI-parsed from docs.sarvam.ai (Saaras + Saarika families)
  - TTS models : AI-parsed from docs.sarvam.ai (Bulbul family)
  - TTS voices : AI-parsed from docs.sarvam.ai (speakers listed under TTS API reference)

Indian AI provider specializing in Indian languages. First provider where
models, voices, AND deprecation status all come from documentation — no API
exposes this metadata.

SARVAM_API_KEY is reserved for future use — all sync data currently comes from
public documentation. Only ANTHROPIC_API_KEY is needed for the AI parser.

STT models, TTS models, and TTS voices parse concurrently via asyncio.gather.
"""

from __future__ import annotations

import asyncio
import sys

from naaviq.sync.ai_parser import AIParserError, parse_models_from_docs, parse_voices_from_docs
from naaviq.sync.base import ProviderSyncer, SyncResult

_STT_DOCS = [
    "https://docs.sarvam.ai/api-reference-docs/speech-to-text/transcribe",
    "https://docs.sarvam.ai/api-reference-docs/api-guides-tutorials/speech-to-text/overview",
    "https://docs.sarvam.ai/api-reference-docs/getting-started/models",
]

_TTS_DOCS = [
    "https://docs.sarvam.ai/api-reference-docs/text-to-speech/convert",
    "https://docs.sarvam.ai/api-reference-docs/getting-started/models",
]

_VOICES_DOCS = [
    "https://docs.sarvam.ai/api-reference-docs/text-to-speech/convert",
]

_STT_GUIDANCE = (
    "Sarvam's STT product has two model families: Saaras and Saarika. "
    "Return ALL listed models using their exact model_id strings (e.g., 'saaras:v3', "
    "'saarika:v2.5'). Mark the recommended/latest one as is_default=true; only ONE "
    "model total should have is_default=true. "
    "Languages MUST be BCP-47 with uppercase region (hi-IN, ta-IN, en-IN, bn-IN, etc.). "
    "Set streaming=true — Sarvam offers WebSocket streaming for STT. "
    "If the models page says a model is 'phased out' or 'legacy', set "
    "eol_date to today's date if no specific date is given. "
    "Populate `meta` with capability flags the docs mention: "
    "  diarization         — true if supported (note if batch-only)"
    "  auto_language_detect — true if the model can auto-detect languages"
    "  code_mixing          — true if the model handles mixed-language speech"
    "  output_modes         — list of modes like ['transcribe','translate','verbatim','translit','codemix']"
    "  max_audio_duration   — e.g., '30s' for REST, '1h' for batch, if documented"
)

_TTS_GUIDANCE = (
    "Sarvam's TTS product is the Bulbul family. Return ALL listed models using their "
    "exact model_id strings (e.g., 'bulbul:v3', 'bulbul:v2'). Mark the latest/recommended "
    "one as is_default=true; only ONE model total should have is_default=true. "
    "Languages MUST be BCP-47 with uppercase region (hi-IN, ta-IN, en-IN, etc.). "
    "Set streaming=true — Sarvam offers HTTP + WebSocket streaming for TTS. "
    "If the models page says a model is 'phased out' or 'legacy' or not listed at all, "
    "set eol_date to today's date if no specific date is given. "
    "Populate `meta` with: "
    "  max_characters       — character limit per request if documented"
    "  temperature_control  — true if the model supports temperature"
    "  pitch_control        — true if the model supports pitch adjustment"
    "  loudness_control     — true if the model supports loudness adjustment"
    "  num_speakers         — number of available speakers/voices"
)

_VOICES_GUIDANCE = (
    "Return EVERY speaker/voice listed under the TTS speaker parameter. "
    "voice_id = the exact lowercase speaker name string used in the API (e.g., 'shubh', "
    "'anushka', 'aditya'). display_name = title-cased voice_id (e.g., 'Shubh'). "
    "If the docs specify gender for a speaker, set it ('male'/'female'). Otherwise leave "
    "gender=null — do NOT guess from the name. "
    "category='premade' for all speakers. "
    "The docs list supported languages at the model level, not per-speaker — but ALL speakers "
    "for a given model support ALL of that model's languages. Copy the model's full language "
    "list onto EVERY voice in BCP-47 format (hi-IN, ta-IN, bn-IN, en-IN, etc.). "
    "Do NOT return an empty languages list — every voice must have languages populated. "
    "In meta, note: "
    "  compatible_model — which model(s) the speaker works with (e.g., 'bulbul:v3' or 'bulbul:v2')"
    "  is_default_speaker — true only for the default speaker of each model"
)


class SarvamSyncer(ProviderSyncer):
    provider_id = "sarvam"
    source = "docs"

    async def sync(self) -> SyncResult:
        from naaviq.config import settings
        ai_key = settings.anthropic_api_key or None

        (stt_models, stt_notes), (tts_models, tts_notes), (tts_voices, voice_notes) = (
            await asyncio.gather(
                parse_models_from_docs(
                    seed_urls=_STT_DOCS,
                    provider_id=self.provider_id,
                    model_type="stt",
                    guidance=_STT_GUIDANCE,
                    api_key=ai_key,
                ),
                parse_models_from_docs(
                    seed_urls=_TTS_DOCS,
                    provider_id=self.provider_id,
                    model_type="tts",
                    guidance=_TTS_GUIDANCE,
                    api_key=ai_key,
                ),
                parse_voices_from_docs(
                    seed_urls=_VOICES_DOCS,
                    provider_id=self.provider_id,
                    guidance=_VOICES_GUIDANCE,
                    api_key=ai_key,
                ),
            )
        )

        total_in  = stt_notes["input_tokens"]  + tts_notes["input_tokens"]  + voice_notes["input_tokens"]
        total_out = stt_notes["output_tokens"] + tts_notes["output_tokens"] + voice_notes["output_tokens"]
        notes = (
            f"STT models: {len(stt_models)} from {len(stt_notes['urls_fetched'])} page(s). "
            f"TTS models: {len(tts_models)} from {len(tts_notes['urls_fetched'])} page(s). "
            f"TTS voices: {len(tts_voices)} from {len(voice_notes['urls_fetched'])} page(s). "
            f"AI: {total_in} in / {total_out} out tokens ({stt_notes['model']})."
        )

        return SyncResult(
            stt_models=stt_models,
            tts_models=tts_models,
            tts_voices=tts_voices,
            source=self.source,
            notes=notes,
        )


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    syncer = SarvamSyncer()
    try:
        result = await syncer.sync()
    except AIParserError as e:
        print(f"\nError: {e}", file=sys.stderr)
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
        print(f"  {m.model_id!r:25} {m.display_name!r:30} langs={len(m.languages)}{marker}")
        if m.meta:
            print(f"    meta: {m.meta}")

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) ===")
    for v in result.tts_voices:
        print(
            f"  {v.voice_id!r:15} {v.display_name!r:15} "
            f"gender={v.gender} langs={v.languages} meta={v.meta}"
        )

    if result.notes:
        print(f"\n=== Notes ===\n  {result.notes}")


if __name__ == "__main__":
    asyncio.run(_main())
