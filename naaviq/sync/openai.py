"""
OpenAI sync script.

Source: docs
  - Models (TTS + STT) : AI-parsed from developers.openai.com/api/docs/models/all
  - TTS voices         : AI-parsed from developers.openai.com/api/docs/guides/text-to-speech

The TTS and STT model-parse calls share the same seed URL so Anthropic prompt
caching reduces cost across the two calls. OPENAI_API_KEY is reserved for
future use — all data currently comes from public documentation.
"""

from __future__ import annotations

import asyncio
import sys

from naaviq.config import settings
from naaviq.sync.ai_parser import AIParserError, parse_models_from_docs, parse_voices_from_docs
from naaviq.sync.base import ProviderSyncer, SyncResult

_MODELS_DOCS = [
    "https://developers.openai.com/api/docs/models/all",
]

_VOICES_DOCS = [
    "https://developers.openai.com/api/docs/guides/text-to-speech",
]

_TTS_GUIDANCE = (
    "Return every model listed under the 'Text-to-speech' section on this page. "
    "Ignore realtime, audio-preview, and chat models. "
    "Mark the newest/recommended TTS model as is_default=true. "
    "In meta, note each model's tradeoff (speed vs quality) and whether it supports voice control."
)

_STT_GUIDANCE = (
    "Return every model listed under the 'Speech-to-text' section on this page. "
    "Ignore realtime, audio-preview, and chat models. "
    "Mark the newest/recommended dedicated transcription model as is_default=true. "
    "streaming=false (OpenAI STT is batch-only via the transcriptions endpoint). "
    "In meta, capture per-model capabilities like diarization when the docs mention them."
)

_VOICES_GUIDANCE = (
    "Return every built-in TTS voice listed on this page. "
    "voice_id = the exact lowercase string used in the API; display_name = title-cased voice_id. "
    "OpenAI docs don't publish per-voice gender — leave gender=null. "
    "For compatible_models: if a voice only works with a specific model (e.g., gpt-4o-mini-tts only), "
    "set compatible_models=['gpt-4o-mini-tts']. If a voice works with all TTS models (tts-1, tts-1-hd, "
    "gpt-4o-mini-tts), set compatible_models=[]. "
    "In meta, note any voices the docs flag as recommended."
)


class OpenAISyncer(ProviderSyncer):
    provider_id = "openai"
    source = "docs"

    async def sync(self) -> SyncResult:
        ai_key = settings.anthropic_api_key or None

        (tts_models, tts_notes), (stt_models, stt_notes), (tts_voices, voice_notes) = (
            await asyncio.gather(
                parse_models_from_docs(
                    seed_urls=_MODELS_DOCS,
                    provider_id=self.provider_id,
                    model_type="tts",
                    guidance=_TTS_GUIDANCE,
                    api_key=ai_key,
                ),
                parse_models_from_docs(
                    seed_urls=_MODELS_DOCS,
                    provider_id=self.provider_id,
                    model_type="stt",
                    guidance=_STT_GUIDANCE,
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

        total_in  = tts_notes["input_tokens"]  + stt_notes["input_tokens"]  + voice_notes["input_tokens"]
        total_out = tts_notes["output_tokens"] + stt_notes["output_tokens"] + voice_notes["output_tokens"]
        notes = (
            f"TTS models: {len(tts_models)} from {len(tts_notes['urls_fetched'])} page(s). "
            f"STT models: {len(stt_models)} from {len(stt_notes['urls_fetched'])} page(s). "
            f"TTS voices: {len(tts_voices)} from {len(voice_notes['urls_fetched'])} page(s). "
            f"AI: {total_in} in / {total_out} out tokens ({tts_notes['model']})."
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
    syncer = OpenAISyncer()
    try:
        result = await syncer.sync()
    except AIParserError as e:
        print(f"\nError: {e}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        marker = " [default]" if m.is_default else ""
        print(f"  {m.model_id!r:30} {m.display_name!r:30} langs={m.languages}{marker}")
        if m.meta:
            print(f"    meta: {m.meta}")

    print(f"\n=== STT Models ({len(result.stt_models)}) ===")
    for m in result.stt_models:
        marker = " [default]" if m.is_default else ""
        print(f"  {m.model_id!r:30} {m.display_name!r:30} langs={m.languages}{marker}")
        if m.meta:
            print(f"    meta: {m.meta}")

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) ===")
    for v in result.tts_voices:
        print(
            f"  {v.voice_id!r:10} {v.display_name!r:12} "
            f"gender={v.gender} langs={v.languages} meta={v.meta}"
        )

    if result.notes:
        print(f"\n=== Notes ===\n  {result.notes}")


if __name__ == "__main__":
    asyncio.run(_main())
