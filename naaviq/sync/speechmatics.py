"""
Speechmatics sync script.

Source: docs
  - STT models: AI-parsed from docs — enhanced, default, medical
  - TTS: skipped — TTS is in preview, English-only, no stable list endpoint
  - tts_models=[], tts_voices=[]

STT is the core product: 55+ languages, batch + real-time WebSocket.

Auth: SPEECHMATICS_API_KEY env var (not needed for docs parsing).
"""

from __future__ import annotations

import asyncio

from naaviq.config import settings  # noqa: F401 — triggers load_dotenv() for ANTHROPIC_API_KEY
from naaviq.sync.ai_parser import parse_models_from_docs
from naaviq.sync.base import ProviderSyncer, SyncResult

_STT_SEED_URLS = ["https://docs.speechmatics.com/"]
_STT_MODEL_GUIDANCE = (
    "Extract Speechmatics STT models. "
    "There are three: 'enhanced' (maximum accuracy, is_default=True), "
    "'default' (faster, slightly lower accuracy), and 'medical' (English-only, medical domain). "
    "enhanced and default support 55+ languages — use ['*'] for their languages. "
    "medical is English-only — use ['en']. "
    "All support streaming (real-time WebSocket API). "
    "Use exact model_id values: 'enhanced', 'default', 'medical'."
)


class SpeechmaticsSyncer(ProviderSyncer):
    provider_id = "speechmatics"
    source = "docs"

    async def sync(self) -> SyncResult:
        stt_models, notes = await parse_models_from_docs(
            seed_urls=_STT_SEED_URLS,
            provider_id=self.provider_id,
            model_type="stt",
            guidance=_STT_MODEL_GUIDANCE,
        )
        return SyncResult(
            stt_models=stt_models,
            tts_models=[],
            tts_voices=[],
            source=self.source,
            docs_urls=_STT_SEED_URLS,
            notes=notes,
        )


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    syncer = SpeechmaticsSyncer()
    result = await syncer.sync()

    print(f"\n=== STT Models ({len(result.stt_models)}) ===")
    for m in result.stt_models:
        print(
            f"  {m.model_id!r:15} {m.display_name!r:20} "
            f"langs={m.languages} is_default={m.is_default}"
        )

    print(f"\nSource: {result.source}")
    print(f"Fetched at: {result.fetched_at}")
    if result.notes:
        print(f"\nNotes: {result.notes}")


if __name__ == "__main__":
    asyncio.run(_main())
