"""
Cartesia sync script.

Source: mixed
  - TTS voices : GET https://api.cartesia.ai/voices  (cursor-paginated REST API)
  - TTS models : AI-parsed from https://docs.cartesia.ai/build-with-cartesia/tts-models/latest
  - STT models : AI-parsed from https://docs.cartesia.ai/build-with-cartesia/stt-models   (Ink family)

The voice API returns one entry per voice. We filter to is_public=True voices
(community-cloned and private voices are skipped).

The TTS models page lists family models (sonic-3, sonic-2, sonic-turbo, sonic),
dated snapshots (e.g., sonic-3-2026-01-12), and "-latest" aliases. We return
all of them — each is a real callable model ID — and stash the snapshot/alias
metadata in `meta` so consumers can filter (e.g., is_snapshot=false for the
4-family view). The STT page follows the same convention.

Voices and both parse calls run concurrently via asyncio.gather to keep total
sync latency around the slowest single call (~10s) instead of the sum (~30s).
"""

from __future__ import annotations

import asyncio

import httpx

from naaviq.config import settings
from naaviq.sync.ai_parser import AIParserError, parse_models_from_docs
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncResult, SyncVoice
from naaviq.sync.language import normalize_languages

_VOICES_URL = "https://api.cartesia.ai/voices"

_TTS_MODELS_DOCS_URLS = [
    "https://docs.cartesia.ai/build-with-cartesia/tts-models/latest",
    "https://docs.cartesia.ai/build-with-cartesia/tts-models/older-models",
]
_STT_MODELS_DOCS_URLS = [
    "https://docs.cartesia.ai/build-with-cartesia/stt-models",
]

_CARTESIA_VERSION = "2025-04-16"  # TODO: verify current value at docs.cartesia.ai
_PAGE_SIZE        = 100
_MAX_PAGES        = 50

_GENDER_MAP = {
    "masculine":      "male",
    "feminine":       "female",
    "gender_neutral": "neutral",
}

# BCP-47 region → accent label (used when voice language is regionalized)
_ACCENT_MAP = {
    "GB": "british",
    "US": "american",
    "AU": "australian",
    "IN": "indian",
    "CA": "canadian",
    "IE": "irish",
    "ZA": "south_african",
    "NZ": "new_zealander",
}

_META_GUIDANCE = (
    "Set eol_date='YYYY-MM-DD' (top-level field, not in meta) if the docs mention an end-of-life date. "
    "For every model, populate `meta` with these keys: "
    "  parent_model_id        — the family root id (e.g., 'sonic-3', 'ink-whisper'), or null if this IS the root"
    "  is_snapshot            — true for dated snapshots, else false"
    "  snapshot_date          — 'YYYY-MM-DD' for dated snapshots, else null"
    "  production_recommended — false for '-latest' aliases (Cartesia warns against these in prod), else true"
)

_TTS_MODELS_GUIDANCE = (
    "Cartesia publishes TTS family models (sonic-3, sonic-2, sonic-turbo, sonic), "
    "dated snapshots (e.g., sonic-3-2026-01-12), and '-latest' aliases. "
    "Return ALL of them as separate models — each is a real callable model ID. "
    "Mark the newest family root as is_default=true (currently sonic-3); only "
    "ONE model total should have is_default=true. "
    + _META_GUIDANCE
)

_STT_MODELS_GUIDANCE = (
    "Cartesia's STT product is the Ink family. Return ALL listed STT models — "
    "family roots, dated snapshots, and '-latest' aliases. Mark the newest "
    "production-ready family root as is_default=true; only ONE model total "
    "should have is_default=true. "
    + _META_GUIDANCE
)


class CartesiaSyncer(ProviderSyncer):
    provider_id = "cartesia"
    source = "mixed"

    async def sync(self) -> SyncResult:
        if not settings.cartesia_api_key:
            raise ValueError("CARTESIA_API_KEY is not set in .env")

        ai_key = settings.anthropic_api_key or None

        (tts_voices, voice_pages), (tts_models, tts_notes), (stt_models, stt_notes) = await asyncio.gather(
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

        total_in  = tts_notes["input_tokens"]  + stt_notes["input_tokens"]
        total_out = tts_notes["output_tokens"] + stt_notes["output_tokens"]
        notes = (
            f"Voices: {len(tts_voices)} from {voice_pages} API page(s). "
            f"TTS models: {len(tts_models)} from {len(tts_notes['urls_fetched'])} doc page(s). "
            f"STT models: {len(stt_models)} from {len(stt_notes['urls_fetched'])} doc page(s). "
            f"AI: {total_in} in / {total_out} out tokens ({tts_notes['model']})."
        )

        return SyncResult(
            stt_models=stt_models,
            tts_models=tts_models,
            tts_voices=tts_voices,
            source=self.source,
            notes=notes,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_voices(self) -> tuple[list[SyncVoice], int]:
        """Returns (voices, page_count). Caps at _MAX_PAGES to prevent cursor loops."""
        voices: list[SyncVoice] = []
        starting_after: str | None = None
        pages = 0

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            for _ in range(_MAX_PAGES):
                params: dict = {
                    "limit": _PAGE_SIZE,
                    "expand[]": "preview_file_url",
                }
                if starting_after:
                    params["starting_after"] = starting_after

                resp = await client.get(
                    _VOICES_URL,
                    headers={
                        "Authorization":   f"Bearer {settings.cartesia_api_key}",
                        "Cartesia-Version": _CARTESIA_VERSION,
                    },
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
                pages += 1

                items = data.get("data", [])
                for item in items:
                    if not item.get("is_public", False):
                        continue
                    voices.append(_to_sync_voice(item))

                if not data.get("has_more", False) or not items:
                    break
                starting_after = items[-1].get("id")
                if not starting_after:
                    break
            else:
                raise RuntimeError(
                    f"Cartesia voice pagination exceeded {_MAX_PAGES} pages — possible cursor loop"
                )

        return voices, pages


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_sync_voice(item: dict) -> SyncVoice:
    raw_lang = item.get("language") or ""
    languages = normalize_languages([raw_lang]) if raw_lang else []

    return SyncVoice(
        voice_id=item["id"],
        display_name=item.get("name") or item["id"],
        gender=_GENDER_MAP.get(item.get("gender") or ""),
        category="premade",
        languages=languages,
        description=item.get("description"),
        preview_url=item.get("preview_file_url"),
        accent=_accent_from_languages(languages),
        meta={},
    )


def _accent_from_languages(languages: list[str]) -> str | None:
    """Derive accent from BCP-47 region. e.g., 'en-GB' → 'british'."""
    for lang in languages:
        parts = lang.split("-")
        if len(parts) >= 2 and parts[1].upper() in _ACCENT_MAP:
            return _ACCENT_MAP[parts[1].upper()]
    return None


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    syncer = CartesiaSyncer()
    try:
        result = await syncer.sync()
    except (ValueError, AIParserError, RuntimeError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        raise SystemExit(1)
    except httpx.HTTPStatusError as e:
        print(f"\nCartesia API error ({e.response.status_code}): {e.response.text}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== STT Models ({len(result.stt_models)}) ===")
    for m in result.stt_models:
        marker = " [default]" if m.is_default else ""
        print(f"  {m.model_id!r:30} {m.display_name!r:30} langs={len(m.languages)}{marker}")
        if m.meta:
            print(f"    meta: {m.meta}")

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        marker = " [default]" if m.is_default else ""
        print(f"  {m.model_id!r:30} {m.display_name!r:30} langs={len(m.languages)}{marker}")
        if m.meta:
            print(f"    meta: {m.meta}")

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) — showing first 20 ===")
    for v in result.tts_voices[:20]:
        print(
            f"  {v.voice_id!r:40} {v.display_name!r:25} "
            f"gender={v.gender} accent={v.accent} lang={v.languages}"
        )
    if len(result.tts_voices) > 20:
        print(f"  ... and {len(result.tts_voices) - 20} more")

    print(f"\nSource: {result.source}")
    print(f"Notes:  {result.notes}")
    print(f"Fetched at: {result.fetched_at}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(_main())
