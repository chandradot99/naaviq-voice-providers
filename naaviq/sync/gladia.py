"""
Gladia sync script.

Source: docs
  - STT models: AI-parsed from docs
  - TTS: not offered — tts_models=[], tts_voices=[]

Gladia is STT-only. One primary model:
  - solaria-1 (default): supports batch (pre-recorded) and live (WebSocket streaming),
    130+ languages using ISO 639-1 codes (ISO 639-3 for languages without a 639-1 code,
    e.g. 'haw' for Hawaiian). Supports automatic language detection and code-switching.

Auth: x-gladia-key header, GLADIA_API_KEY env var.
Not needed for sync — models are parsed from public docs.
"""

from __future__ import annotations

import asyncio

from naaviq.config import settings  # noqa: F401 — triggers load_dotenv() for ANTHROPIC_API_KEY
from naaviq.sync.ai_parser import notes_to_str, parse_models_from_docs
from naaviq.sync.base import ProviderSyncer, SyncResult

_SEED_URLS = [
    "https://docs.gladia.io/chapters/live-stt/quickstart.md",
    "https://docs.gladia.io/chapters/language/supported-languages.md",
]

_MODEL_GUIDANCE = (
    "Extract Gladia STT models. There is currently one model: solaria-1.\n"
    "\n"
    "  - solaria-1: 'Solaria-1', is_default=True, streaming=True (supports both batch "
    "and live WebSocket streaming), languages=['*'] (130+ languages via ISO 639-1 codes, "
    "with ISO 639-3 codes for languages that have no 639-1 code such as Hawaiian 'haw'). "
    "Supports automatic language detection and code-switching.\n"
    "\n"
    "Use exact model_id: 'solaria-1'. Mark it is_default=True."
)


class GladiaSyncer(ProviderSyncer):
    provider_id = "gladia"
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
    syncer = GladiaSyncer()
    result = await syncer.sync()

    print(f"\n=== STT Models ({len(result.stt_models)}) ===")
    for m in result.stt_models:
        stream_label = "streaming" if m.streaming else "batch   "
        print(
            f"  {m.model_id!r:20} [{stream_label}] "
            f"langs={m.languages} is_default={m.is_default}"
        )
        if m.description:
            print(f"    {m.description}")

    print(f"\nSource: {result.source}")
    print(f"Docs  : {result.docs_urls}")
    print(f"Fetched at: {result.fetched_at}")
    if result.notes:
        print(f"\nNotes: {result.notes}")


if __name__ == "__main__":
    asyncio.run(_main())
