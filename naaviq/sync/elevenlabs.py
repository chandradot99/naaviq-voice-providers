"""
ElevenLabs sync script.

Source: mixed
  - TTS models : GET /v1/models (entries with can_do_text_to_speech=true)
  - STT models : AI-parsed from https://elevenlabs.io/docs/capabilities/speech-to-text
                 (Scribe family — not returned by /v1/models)
  - TTS voices : GET /v2/voices (premade voices, server-side filtered, paginated)

Auth: xi-api-key header.

TTS models, voices, and the STT parse all run concurrently via asyncio.gather.
"""

from __future__ import annotations

import asyncio

import httpx

from naaviq.config import settings
from naaviq.sync.ai_parser import AIParserError, parse_models_from_docs
from naaviq.sync.base import ProviderSyncer, SyncModel, SyncResult, SyncVoice
from naaviq.sync.language import normalize_languages

_MODELS_URL = "https://api.elevenlabs.io/v1/models"
_VOICES_URL = "https://api.elevenlabs.io/v2/voices"

_STT_MODELS_DOCS_URLS = [
    "https://elevenlabs.io/docs/capabilities/speech-to-text",
]

_VOICE_PAGE_SIZE = 100   # ElevenLabs v2 max is 100
_MAX_VOICE_PAGES = 20    # safety cap; 100 × 20 = 2000 voices ceiling

_GENDER_MAP = {
    "male":       "male",
    "female":     "female",
    "neutral":    "neutral",
    "non-binary": "neutral",
}

_STT_MODELS_GUIDANCE = (
    "ElevenLabs' STT product is the Scribe family. Return ALL listed Scribe "
    "models with their actual model_id strings (e.g., 'scribe_v1', "
    "'scribe_v1_experimental'). Mark the production-recommended one as "
    "is_default=true; only ONE model total should have is_default=true. "
    "Scribe is multilingual (~99 languages); if the docs don't enumerate them, "
    "use ['*'] for languages. Set streaming=true if the docs mention realtime/"
    "streaming support, else false. Populate `meta` with capability flags the "
    "docs mention: diarization, word_timestamps, experimental, etc."
)


class ElevenLabsSyncer(ProviderSyncer):
    provider_id = "elevenlabs"
    source = "mixed"

    async def sync(self) -> SyncResult:
        if not settings.elevenlabs_api_key:
            raise ValueError("ELEVENLABS_API_KEY is not set in .env")

        ai_key = settings.anthropic_api_key or None

        models_data, voices_data, (stt_models, stt_notes) = await asyncio.gather(
            self._fetch_models(),
            self._fetch_voices(),
            parse_models_from_docs(
                seed_urls=_STT_MODELS_DOCS_URLS,
                provider_id=self.provider_id,
                model_type="stt",
                guidance=_STT_MODELS_GUIDANCE,
                api_key=ai_key,
            ),
        )

        api_stt_models, tts_models = self._parse_models(models_data)
        tts_voices = self._parse_voices(voices_data)

        # If /v1/models ever starts returning STT entries, merge them in
        # (skipping any IDs the AI parser already produced).
        ai_stt_ids = {m.model_id for m in stt_models}
        for api_m in api_stt_models:
            if api_m.model_id not in ai_stt_ids:
                stt_models.append(api_m)

        notes = (
            f"TTS models: {len(tts_models)} from /v1/models. "
            f"STT models: {len(stt_models)} from {len(stt_notes['urls_fetched'])} doc page(s). "
            f"Voices: {len(tts_voices)} from /v2/voices. "
            f"AI: {stt_notes['input_tokens']} in / {stt_notes['output_tokens']} out tokens ({stt_notes['model']})."
        )

        return SyncResult(
            stt_models=stt_models,
            tts_models=tts_models,
            tts_voices=tts_voices,
            source=self.source,
            notes=notes,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_models(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(_MODELS_URL, headers=self._auth_headers())
            resp.raise_for_status()
            return resp.json()

    async def _fetch_voices(self) -> list[dict]:
        voices: list[dict] = []
        next_token: str | None = None

        async with httpx.AsyncClient(timeout=15.0) as client:
            for _ in range(_MAX_VOICE_PAGES):
                params: dict = {
                    "page_size": _VOICE_PAGE_SIZE,
                    "category":  "premade",  # server-side filter
                }
                if next_token:
                    params["next_page_token"] = next_token

                resp = await client.get(_VOICES_URL, headers=self._auth_headers(), params=params)
                resp.raise_for_status()
                data = resp.json()

                voices.extend(data.get("voices", []))

                if not data.get("has_more", False):
                    break
                next_token = data.get("next_page_token")
                if not next_token:
                    break
            else:
                raise RuntimeError(
                    f"ElevenLabs voice pagination exceeded {_MAX_VOICE_PAGES} pages — possible cursor loop"
                )

        return voices

    def _auth_headers(self) -> dict[str, str]:
        return {"xi-api-key": settings.elevenlabs_api_key}

    def _parse_models(self, data: list[dict]) -> tuple[list[SyncModel], list[SyncModel]]:
        stt_models: list[SyncModel] = []
        tts_models: list[SyncModel] = []

        for m in data:
            languages = normalize_languages([
                lang["language_id"]
                for lang in (m.get("languages") or [])
                if lang.get("language_id")
            ])
            meta = {
                "max_characters":    m.get("maximum_text_length_per_request"),
                "can_be_finetuned":  m.get("can_be_finetuned", False),
                "requires_alpha":    m.get("requires_alpha_access", False),
                "concurrency_group": m.get("concurrency_group"),
                "token_cost_factor": m.get("token_cost_factor"),
            }

            if m.get("can_do_text_to_speech"):
                tts_models.append(SyncModel(
                    model_id=m["model_id"],
                    display_name=m.get("name") or m["model_id"],
                    type="tts",
                    languages=languages,
                    streaming=True,
                    is_default=False,  # ElevenLabs API doesn't signal a default
                    description=m.get("description"),
                    meta=meta,
                ))
            if m.get("can_do_speech_to_text"):
                stt_models.append(SyncModel(
                    model_id=m["model_id"],
                    display_name=m.get("name") or m["model_id"],
                    type="stt",
                    languages=languages,
                    streaming=False,  # Scribe is batch-only at launch; revisit if API signals streaming
                    is_default=False,
                    description=m.get("description"),
                    meta=meta,
                ))

        return stt_models, tts_models

    def _parse_voices(self, items: list[dict]) -> list[SyncVoice]:
        voices: list[SyncVoice] = []
        for v in items:
            # Defense in depth — server already filters category=premade.
            if v.get("category") != "premade":
                continue

            labels = v.get("labels") or {}

            verified = [
                lang.get("language")
                for lang in (v.get("verified_languages") or [])
                if lang.get("language")
            ]

            descriptive = labels.get("descriptive") or labels.get("description") or ""
            tags = [t.strip() for t in descriptive.split(",") if t.strip()] if descriptive else []

            voices.append(SyncVoice(
                voice_id=v["voice_id"],
                display_name=v.get("name") or v["voice_id"],
                gender=_GENDER_MAP.get((labels.get("gender") or "").lower()),
                category="premade",
                languages=list(dict.fromkeys(normalize_languages(verified))),
                description=v.get("description"),
                preview_url=v.get("preview_url"),
                accent=labels.get("accent"),
                age=labels.get("age"),
                use_cases=[labels["use_case"]] if labels.get("use_case") else [],
                tags=tags,
                meta={
                    "labels": labels,
                    "high_quality_base_model_ids": v.get("high_quality_base_model_ids", []),
                },
            ))
        return voices


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    syncer = ElevenLabsSyncer()
    try:
        result = await syncer.sync()
    except (ValueError, AIParserError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        raise SystemExit(1)
    except httpx.HTTPStatusError as e:
        print(
            f"\nElevenLabs API error ({e.response.status_code}): {e.response.text[:300]}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print(f"\n=== STT Models ({len(result.stt_models)}) ===")
    for m in result.stt_models:
        marker = " [default]" if m.is_default else ""
        print(f"  {m.model_id!r:35} {m.display_name!r:35} langs={len(m.languages)}{marker}")
        if m.meta:
            print(f"    meta: {m.meta}")

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        marker = " [default]" if m.is_default else ""
        print(f"  {m.model_id!r:35} {m.display_name!r:35} langs={len(m.languages)}{marker}")

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) — showing first 20 ===")
    for v in result.tts_voices[:20]:
        print(
            f"  {v.voice_id!r:25} {v.display_name!r:20} "
            f"gender={v.gender} accent={v.accent} age={v.age} langs={v.languages}"
        )
    if len(result.tts_voices) > 20:
        print(f"  ... and {len(result.tts_voices) - 20} more")


if __name__ == "__main__":
    asyncio.run(_main())
