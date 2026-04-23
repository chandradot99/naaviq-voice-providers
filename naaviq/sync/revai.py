"""
Rev AI sync script.

Source: docs
  - STT models: AI-parsed from docs — 3 active AI models
  - TTS: not offered — tts_models=[], tts_voices=[]

Rev AI is STT-only. Three active AI models:
  machine  — Reverb ASR, default, streaming + batch, English (streaming) / 58+ langs (batch)
  low_cost — Reverb Turbo, budget, streaming + batch, English (US)
  fusion   — Whisper Fusion, better rare-word recognition

Skipped:
  machine_v2 — deprecated, silently routes to `machine`
  human      — human transcription service, not an AI model

Auth: Authorization: Bearer <token>, REVAI_API_KEY env var.
Not needed for sync — models are parsed from public docs.
"""

from __future__ import annotations

import asyncio

from naaviq.config import settings  # noqa: F401 — triggers load_dotenv() for ANTHROPIC_API_KEY
from naaviq.sync.ai_parser import notes_to_str, parse_models_from_docs
from naaviq.sync.base import ProviderSyncer, SyncResult

_SEED_URLS = [
    "https://docs.rev.ai/api/asynchronous/transcribers/",
    "https://docs.rev.ai/api/streaming/",
]
_MODEL_GUIDANCE = (
    "Extract Rev AI STT models. Include only the 3 active AI models — skip 'machine_v2' "
    "(deprecated) and 'human' (human transcription, not AI).\n"
    "\n"
    "Models to extract:\n"
    "  - machine: 'Reverb ASR', is_default=True, streaming=True. "
    "Supports 58+ languages for batch (use languages=['*']); "
    "English-only for streaming — set languages=['en'] as the practical default.\n"
    "  - low_cost: 'Reverb Turbo', is_default=False, streaming=True, languages=['en'].\n"
    "  - fusion: 'Whisper Fusion', is_default=False, streaming=True. "
    "Better recognition for rare words and proper nouns. "
    "Check the docs for its language support; if unclear use languages=['en'].\n"
    "\n"
    "Use exact model_id values: 'machine', 'low_cost', 'fusion'."
)


class RevAISyncer(ProviderSyncer):
    provider_id = "revai"
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
            docs_urls=_SEED_URLS,
            notes=notes_to_str(notes),
        )


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    syncer = RevAISyncer()
    result = await syncer.sync()

    print(f"\n=== STT Models ({len(result.stt_models)}) ===")
    for m in result.stt_models:
        stream_label = "streaming" if m.streaming else "batch   "
        print(
            f"  {m.model_id!r:12} [{stream_label}] "
            f"langs={m.languages} is_default={m.is_default}"
        )

    print(f"\nSource: {result.source}")
    print(f"Fetched at: {result.fetched_at}")
    if result.notes:
        print(f"\nNotes: {result.notes}")


if __name__ == "__main__":
    asyncio.run(_main())
