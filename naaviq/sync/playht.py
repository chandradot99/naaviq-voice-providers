"""
Play.ht sync script.

Source: docs
  - TTS models: AI-parsed from docs (Play3.0, PlayDialog family)
  - TTS voices: AI-parsed from docs (400+ prebuilt voices)
  - STT: not offered — stt_models=[]

No API key required — all data is extracted from public documentation.
Voice language format: full name "English (US)" → BCP-47 via AI parser.

Config: no API key needed for docs-only sync
"""

from __future__ import annotations

import asyncio
import sys

from naaviq.sync.ai_parser import AIParserError, parse_models_from_docs, parse_voices_from_docs
from naaviq.sync.base import ProviderSyncer, SyncResult

_DOCS_URLS = [
    "https://docs.play.ht/reference/models",
    "https://docs.play.ht/reference/list-of-prebuilt-voices",
    "https://docs.play.ht/reference/api-generate-tts-audio-stream",
]

_TTS_MODEL_GUIDANCE = """
Extract Play.ht TTS models. Include only the 3 current active models — skip all legacy models
(PlayHT1.0, PlayHT2.0, PlayHT2.0-turbo).

1. model_id="PlayDialog", display_name="PlayDialog", is_default=True, streaming=True
   - Large expressive English model. Supports multi-turn dialogue. High quality.
   - description="Play.ht PlayDialog — large expressive English TTS with multi-turn dialogue."
   - languages: ["en"]

2. model_id="PlayDialogMultilingual", display_name="PlayDialog Multilingual", is_default=False, streaming=True
   - Large expressive multilingual model. Multi-turn dialogue support.
   - description="Play.ht PlayDialog Multilingual — expressive multilingual TTS with dialogue support."
   - languages: ["*"]

3. model_id="Play3.0-mini", display_name="Play 3.0 Mini", is_default=False, streaming=True
   - Small fast multilingual model. ~143ms TTFB. Best for low-latency applications.
   - description="Play.ht Play 3.0 Mini — ultra-low latency multilingual TTS, ~143ms TTFB."
   - languages: ["*"]
   - meta: {"ttfb_ms": 143}

Use exact model_id values as listed above.
"""

_VOICES_GUIDANCE = """
Extract Play.ht prebuilt TTS voices from the documentation page.

Fields to extract per voice:
- voice_id: the unique voice identifier string from the docs (e.g. "s3://voice-cloning-zero-shot/.../manifest.json" or a short name ID)
- display_name: human-readable voice name (e.g. "William", "Aria")
- gender: "male" | "female" | null
- languages: BCP-47 codes derived from the language field (e.g. "English (US)" → ["en-US"],
  "French" → ["fr"], "Spanish (Spain)" → ["es-ES"], "Spanish (Mexico)" → ["es-MX"],
  "Portuguese (Brazil)" → ["pt-BR"], "Portuguese (Portugal)" → ["pt-PT"])
- accent: accent label if available (e.g. "american", "british", "australian")
- category: "premade"
- compatible_models: [] (all voices work with all Play.ht models)

Extract as many voices as possible from the page. There are 400+ voices across 80+ language variants.
"""


class PlayHTSyncer(ProviderSyncer):
    provider_id = "playht"
    source = "docs"

    async def sync(self) -> SyncResult:
        (tts_models, model_notes), (tts_voices, voice_notes) = await asyncio.gather(
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
            isinstance(model_notes, dict) and model_notes.get("source") == "cache"
            and isinstance(voice_notes, dict) and voice_notes.get("source") == "cache"
        )
        notes = (
            f"{len(tts_models)} TTS models, {len(tts_voices)} voices (cache)."
            if from_cache else
            f"{len(tts_models)} TTS models, {len(tts_voices)} voices."
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
    syncer = PlayHTSyncer()
    try:
        result = await syncer.sync()
    except AIParserError as e:
        print(f"\nError: {e}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        print(
            f"  {m.model_id!r:28} {m.display_name!r:25} "
            f"langs={m.languages} is_default={m.is_default}"
        )

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) ===")
    for v in result.tts_voices[:20]:
        print(
            f"  {v.display_name!r:20} gender={v.gender or '?':7} "
            f"langs={v.languages} accent={v.accent or '—'}"
        )
    if len(result.tts_voices) > 20:
        print(f"  ... and {len(result.tts_voices) - 20} more")

    print(f"\nSource : {result.source}")
    print(f"Notes  : {result.notes}")
    print(f"Fetched: {result.fetched_at}")


if __name__ == "__main__":
    asyncio.run(_main())
