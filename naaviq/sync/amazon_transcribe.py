"""
Amazon Transcribe sync script.

Source: docs
  - STT models: AI-parsed from AWS docs (no /models API endpoint)
  - TTS: not offered — tts_models=[], tts_voices=[]

Three model tiers derived from the AWS supported languages page:
  amazon-transcribe         — batch async, 130+ languages (default)
  amazon-transcribe-stream  — real-time streaming, ~27 languages
  amazon-transcribe-medical — batch, en-US only, healthcare vocabulary + PHI detection

No API key needed for sync — models are parsed from public AWS documentation.

Docs: https://docs.aws.amazon.com/transcribe/latest/dg/supported-languages.html
"""

from __future__ import annotations

import asyncio

from naaviq.config import settings  # noqa: F401 — triggers load_dotenv() for ANTHROPIC_API_KEY
from naaviq.sync.ai_parser import parse_models_from_docs
from naaviq.sync.base import ProviderSyncer, SyncResult

_SEED_URLS = [
    "https://docs.aws.amazon.com/transcribe/latest/dg/supported-languages.html",
    "https://docs.aws.amazon.com/transcribe/latest/dg/streaming.html",
]

_MODEL_GUIDANCE = """
Extract Amazon Transcribe STT models. There are exactly 3 models to return.

1. model_id="amazon-transcribe", display_name="Amazon Transcribe", is_default=True, streaming=False
   - Batch async transcription. Supports 130+ languages.
   - Extract all language codes from the "Supported languages and language-specific features" table.
   - Include every language code listed in the table (BCP-47 format with region, e.g. "en-US", "fr-FR").
   - description="Batch async STT. 130+ languages, custom vocabulary, speaker diarization, PII redaction."

2. model_id="amazon-transcribe-streaming", display_name="Amazon Transcribe Streaming", is_default=False, streaming=True
   - Real-time streaming via HTTP/2 and WebSocket.
   - Only include languages that have streaming support (marked in the "Streaming" or "Data input" column).
   - description="Real-time streaming STT via HTTP/2 and WebSocket."

3. model_id="amazon-transcribe-medical", display_name="Amazon Transcribe Medical", is_default=False, streaming=False
   - Healthcare-specialized batch STT. Only supports en-US.
   - languages=["en-US"]
   - description="Batch STT optimized for healthcare. Medical vocabulary and PHI detection. en-US only."

Use BCP-47 language codes with uppercase region (e.g. "en-US" not "en-us").
All model_id values must be exactly as listed above.
"""


class AmazonTranscribeSyncer(ProviderSyncer):
    provider_id = "amazon-transcribe"
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
            notes=notes,
        )


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    syncer = AmazonTranscribeSyncer()
    try:
        result = await syncer.sync()
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== STT Models ({len(result.stt_models)}) ===")
    for m in result.stt_models:
        stream_label = "streaming" if m.streaming else "batch   "
        marker = " [default]" if m.is_default else ""
        print(
            f"  {m.model_id!r:35} [{stream_label}] "
            f"langs={len(m.languages)}{marker}"
        )

    print(f"\nSource : {result.source}")
    print(f"Notes  : {result.notes}")
    print(f"Fetched: {result.fetched_at}")


if __name__ == "__main__":
    asyncio.run(_main())
