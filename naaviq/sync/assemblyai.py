"""
AssemblyAI sync script.

Source: docs
  - STT models: AI-parsed from docs — 6 models across batch and streaming
  - TTS: not offered — tts_models=[], tts_voices=[]

AssemblyAI is STT-only. Six models across pre-recorded (batch) and streaming (real-time):

  Streaming (real-time WebSocket):
    u3-rt-pro                        — recommended default; 6 langs; <300ms latency
    universal-streaming-multilingual — 6 langs, automatic language detection
    universal-streaming-english      — English-only, cost-effective
    whisper-streaming                — 99+ languages

  Pre-recorded (batch):
    universal-3-pro — highest accuracy, 6 languages, no streaming
    universal-2     — 99+ languages, no streaming

Auth: Authorization: <api_key> (no Bearer prefix), ASSEMBLYAI_API_KEY env var.
Not needed for sync — models are parsed from public docs.
"""

from __future__ import annotations

import asyncio

from naaviq.config import settings  # noqa: F401 — triggers load_dotenv() for ANTHROPIC_API_KEY
from naaviq.sync.ai_parser import parse_models_from_docs
from naaviq.sync.base import ProviderSyncer, SyncResult

_SEED_URLS = ["https://www.assemblyai.com/docs/getting-started/models"]
_MODEL_GUIDANCE = (
    "Extract AssemblyAI STT models. There are 6 models total.\n"
    "\n"
    "Streaming models (streaming=True):\n"
    "  - u3-rt-pro: 'Universal-3 Pro Streaming', is_default=True, languages=['en','es','de','fr','pt','it']\n"
    "  - universal-streaming-multilingual: 'Universal Streaming Multilingual', languages=['en','es','de','fr','pt','it']\n"
    "  - universal-streaming-english: 'Universal Streaming English', languages=['en']\n"
    "  - whisper-streaming: 'Whisper Streaming', languages=['*'] (99+ languages)\n"
    "\n"
    "Batch/pre-recorded models (streaming=False):\n"
    "  - universal-3-pro: 'Universal-3 Pro', languages=['en','es','de','fr','pt','it']\n"
    "  - universal-2: 'Universal-2', languages=['*'] (99+ languages)\n"
    "\n"
    "Use exact model_id values as listed above. "
    "Mark u3-rt-pro as is_default=True (recommended for real-time voice agents). "
    "All others is_default=False."
)


class AssemblyAISyncer(ProviderSyncer):
    provider_id = "assemblyai"
    source = "docs"

    async def sync(self) -> SyncResult:
        stt_models, notes = await parse_models_from_docs(
            seed_urls=_SEED_URLS,
            provider_id=self.provider_id,
            model_type="stt",
            guidance=_MODEL_GUIDANCE,
        )
        return SyncResult(
            stt_models=stt_models,
            tts_models=[],
            tts_voices=[],
            source=self.source,
            notes=notes,
        )


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    syncer = AssemblyAISyncer()
    result = await syncer.sync()

    print(f"\n=== STT Models ({len(result.stt_models)}) ===")
    for m in result.stt_models:
        stream_label = "streaming" if m.streaming else "batch   "
        print(
            f"  {m.model_id!r:38} [{stream_label}] "
            f"langs={m.languages} is_default={m.is_default}"
        )

    print(f"\nSource: {result.source}")
    print(f"Fetched at: {result.fetched_at}")
    if result.notes:
        print(f"\nNotes: {result.notes}")


if __name__ == "__main__":
    asyncio.run(_main())
