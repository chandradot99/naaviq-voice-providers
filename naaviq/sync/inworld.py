"""
Inworld AI sync script.

Source: mixed
  - TTS voices: GET https://api.inworld.ai/voices/v1/voices (API)
  - TTS models: AI-parsed from docs (no /models endpoint)
  - STT models: AI-parsed from docs (no /models endpoint)

Voice API response shape:
  {
    "voices": [...],
    "nextPageToken": "...",
    "totalSize": 135
  }

Each voice:
  {
    "voiceId": "Loretta",
    "name": "Loretta",
    "displayName": "Loretta",
    "langCode": "EN_US",
    "gender": "female",
    "ageGroup": "middle_aged",
    "description": "Inviting, folksy Southern female voice...",
    "tags": ["inviting", "folksy", "southern", ...],
    "categories": ["interactive_media"],
    "source": "SYSTEM"
  }

Auth: Authorization: Basic <base64_key>, INWORLD_API_KEY env var.
"""

from __future__ import annotations

import asyncio

import httpx

from naaviq.config import settings
from naaviq.sync.ai_parser import parse_models_from_docs
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncResult, SyncVoice
from naaviq.sync.language import normalize_languages

_VOICES_URL = "https://api.inworld.ai/voices/v1/voices"

_TTS_DOCS_SEED_URLS = ["https://docs.inworld.ai/tts/tts"]
_TTS_MODEL_GUIDANCE = (
    "Extract Inworld AI TTS models. "
    "Current models: tts-1.5-max (recommended, <250ms P90), tts-1.5-mini (lowest latency, <130ms P90), "
    "tts-1-max (legacy), tts-1 (legacy). "
    "Mark tts-1.5-max as is_default=True. All support streaming. "
    "15 languages: en, es, fr, ko, nl, zh, de, it, ja, pl, pt, ru, hi, ar, he. "
    "Use exact model_id values: 'tts-1.5-max', 'tts-1.5-mini', 'tts-1-max', 'tts-1'."
)

_STT_DOCS_SEED_URLS = ["https://docs.inworld.ai/stt/overview"]
_STT_MODEL_GUIDANCE = (
    "Extract Inworld AI STT models. "
    "Include inworld-stt-1 (English-only, voice profiling with emotion/accent/age/pitch detection) "
    "and groq-whisper-large-v3 (99+ languages via Groq). "
    "Mark inworld-stt-1 as is_default=True. Both support streaming."
)

# Inworld accent labels → our accent format
_ACCENT_MAP: dict[str, str] = {
    "american":   "american",
    "british":    "british",
    "australian": "australian",
    "indian":     "indian",
    "canadian":   "canadian",
    "irish":      "irish",
}


class InworldAISyncer(ProviderSyncer):
    provider_id = "inworld"
    source = "mixed"

    async def sync(self) -> SyncResult:
        voices_data, (tts_models, tts_notes), (stt_models, stt_notes) = await asyncio.gather(
            self._fetch_voices(),
            parse_models_from_docs(
                seed_urls=_TTS_DOCS_SEED_URLS,
                provider_id=self.provider_id,
                model_type="tts",
                guidance=_TTS_MODEL_GUIDANCE,
            ),
            parse_models_from_docs(
                seed_urls=_STT_DOCS_SEED_URLS,
                provider_id=self.provider_id,
                model_type="stt",
                guidance=_STT_MODEL_GUIDANCE,
            ),
        )

        notes_parts = []
        if tts_notes:
            notes_parts.append(f"TTS: {tts_notes}")
        if stt_notes:
            notes_parts.append(f"STT: {stt_notes}")

        return SyncResult(
            stt_models=stt_models,
            tts_models=tts_models,
            tts_voices=self._parse_voices(voices_data),
            source=self.source,
            api_urls=[_VOICES_URL],
            docs_urls=_TTS_DOCS_SEED_URLS + _STT_DOCS_SEED_URLS,
            notes="; ".join(notes_parts) if notes_parts else None,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_voices(self) -> list[dict]:
        if not settings.inworld_api_key:
            raise ValueError("INWORLD_API_KEY is not set in .env")

        headers = {"Authorization": f"Basic {settings.inworld_api_key}"}
        all_voices: list[dict] = []

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            page_token: str | None = None
            while True:
                params: dict[str, str] = {}
                if page_token:
                    params["pageToken"] = page_token

                resp = await client.get(_VOICES_URL, headers=headers, params=params)
                resp.raise_for_status()
                body = resp.json()

                all_voices.extend(body.get("voices", []))

                page_token = body.get("nextPageToken")
                if not page_token:
                    break

        return all_voices

    def _parse_voices(self, voices_data: list[dict]) -> list[SyncVoice]:
        voices: list[SyncVoice] = []
        for v in voices_data:
            if v.get("source") not in ("SYSTEM", None):
                # Skip user-cloned voices if any appear
                pass

            voice_id = v.get("voiceId") or v.get("name")
            if not voice_id:
                continue

            gender = _parse_gender(v.get("gender"))
            age = _parse_age(v.get("ageGroup"))
            accent = _parse_accent_from_tags(v.get("tags") or [])

            lang_code = v.get("langCode", "")
            languages = normalize_languages([_normalize_lang_code(lang_code)]) if lang_code else ["*"]

            meta: dict = {}
            if v.get("tags"):
                meta["tags"] = v["tags"]
            if v.get("categories"):
                meta["categories"] = v["categories"]

            voices.append(SyncVoice(
                voice_id=voice_id,
                display_name=v.get("displayName") or v.get("name") or voice_id,
                gender=gender,
                category="premade",
                languages=languages,
                accent=accent,
                age=age,
                description=v.get("description"),
                compatible_models=[],
                meta=meta,
            ))
        return voices


# ── Field parsers ─────────────────────────────────────────────────────────────

def _parse_gender(value: str | None) -> str | None:
    if not value:
        return None
    g = value.lower()
    if g in ("male", "female", "neutral"):
        return g
    return None


def _parse_age(value: str | None) -> str | None:
    if not value:
        return None
    a = value.lower().replace("-", "_")
    if a in ("age_unspecified", "unspecified"):
        return None
    return a


def _parse_accent_from_tags(tags: list[str]) -> str | None:
    for tag in tags:
        mapped = _ACCENT_MAP.get(tag.lower())
        if mapped:
            return mapped
    return None


def _normalize_lang_code(code: str) -> str:
    """Convert Inworld lang codes like 'EN_US' to BCP-47 'en-US'."""
    return code.replace("_", "-")


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    syncer = InworldAISyncer()
    try:
        result = await syncer.sync()
    except ValueError as e:
        print(f"\nError: {e}", file=sys.stderr)
        raise SystemExit(1)
    except httpx.HTTPStatusError as e:
        print(f"\nInworld API error ({e.response.status_code}): {e.response.text[:300]}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== STT Models ({len(result.stt_models)}) ===")
    for m in result.stt_models:
        print(
            f"  {m.model_id!r:25} {m.display_name!r:30} "
            f"langs={m.languages} is_default={m.is_default}"
        )

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        print(
            f"  {m.model_id!r:20} {m.display_name!r:20} "
            f"langs={len(m.languages)} is_default={m.is_default}"
        )

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) ===")
    for v in result.tts_voices:
        print(
            f"  {v.voice_id!r:20} {v.display_name!r:20} "
            f"gender={v.gender or '?':6} accent={v.accent or '?':12} "
            f"age={v.age or '?':10} langs={v.languages}"
        )

    print(f"\nSource: {result.source}")
    print(f"Fetched at: {result.fetched_at}")
    if result.notes:
        print(f"\nNotes: {result.notes}")


if __name__ == "__main__":
    asyncio.run(_main())
