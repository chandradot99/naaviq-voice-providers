"""
Amazon Transcribe sync script.

Source: docs
  - STT models: hardcoded (no /models API endpoint — static language list from AWS docs)
  - TTS: not offered — tts_models=[], tts_voices=[]

Three model tiers:
  amazon-transcribe         — batch async, 130+ languages (default)
  amazon-transcribe-stream  — real-time streaming, ~60 languages
  amazon-transcribe-medical — batch, en-US only, healthcare vocabulary + PHI detection

Auth: reuses AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY + AWS_REGION (same as Amazon Polly).
No API calls are made — language lists are static per AWS documentation.

Docs: https://docs.aws.amazon.com/transcribe/latest/dg/supported-languages.html
"""

from __future__ import annotations

import asyncio

from naaviq.config import settings
from naaviq.sync.base import ProviderSyncer, SyncModel, SyncResult
from naaviq.sync.language import normalize_languages

_DOCS_URLS = [
    "https://docs.aws.amazon.com/transcribe/latest/dg/supported-languages.html",
    "https://docs.aws.amazon.com/transcribe/latest/dg/streaming.html",
]

# All languages supported for batch transcription (130+ from AWS docs)
_BATCH_LANGUAGES = normalize_languages([
    "ab-GE", "af-ZA", "ar-AE", "ar-SA", "hy-AM", "ast-ES", "az-AZ",
    "ba-RU", "eu-ES", "be-BY", "bn-IN", "bs-BA", "bg-BG", "ca-ES",
    "ckb-IR", "ckb-IQ", "zh-CN", "zh-TW", "yue-HK", "hr-HR", "cs-CZ",
    "da-DK", "nl-NL", "en-AU", "en-GB", "en-IN", "en-IE", "en-NZ",
    "en-ZA", "en-US", "et-EE", "fa-IR", "fi-FI", "fr-FR", "fr-CA",
    "gl-ES", "ka-GE", "de-DE", "de-CH", "el-GR", "gu-IN", "ha-NG",
    "he-IL", "hi-IN", "hu-HU", "is-IS", "id-ID", "it-IT", "ja-JP",
    "kn-IN", "kk-KZ", "rw-RW", "ko-KR", "ky-KG", "lv-LV", "lt-LT",
    "mk-MK", "ms-MY", "ml-IN", "mt-MT", "mr-IN", "mn-MN", "no-NO",
    "or-IN", "ps-AF", "pl-PL", "pt-PT", "pt-BR", "pa-IN", "ro-RO",
    "ru-RU", "sr-RS", "si-LK", "sk-SK", "sl-SI", "so-SO", "es-ES",
    "es-US", "su-ID", "sw-KE", "sw-BI", "sw-RW", "sw-TZ", "sw-UG",
    "sv-SE", "tl-PH", "ta-IN", "te-IN", "th-TH", "tr-TR", "uk-UA",
    "uz-UZ", "vi-VN", "cy-WL", "wo-SN", "zu-ZA",
])

# Languages supported for real-time streaming (subset of batch)
_STREAMING_LANGUAGES = normalize_languages([
    "zh-CN", "da-DK", "nl-NL", "en-AU", "en-GB", "en-IN", "en-IE",
    "en-NZ", "en-ZA", "en-US", "fr-FR", "fr-CA", "de-DE", "de-CH",
    "he-IL", "hi-IN", "id-ID", "it-IT", "ja-JP", "ko-KR", "ms-MY",
    "pt-PT", "pt-BR", "es-ES", "es-US", "th-TH", "tr-TR",
])

_MODELS: list[tuple[str, str, bool, list[str], bool, str]] = [
    # (model_id, display_name, is_default, languages, streaming, description)
    (
        "amazon-transcribe",
        "Amazon Transcribe",
        True,
        _BATCH_LANGUAGES,
        False,
        "Batch async STT. 130+ languages, custom vocabulary, speaker diarization, PII redaction.",
    ),
    (
        "amazon-transcribe-streaming",
        "Amazon Transcribe Streaming",
        False,
        _STREAMING_LANGUAGES,
        True,
        "Real-time streaming STT via HTTP/2 and WebSocket. ~27 languages.",
    ),
    (
        "amazon-transcribe-medical",
        "Amazon Transcribe Medical",
        False,
        normalize_languages(["en-US"]),
        False,
        "Batch STT optimized for healthcare. Medical vocabulary, PHI detection. en-US only.",
    ),
]


class AmazonTranscribeSyncer(ProviderSyncer):
    provider_id = "amazon-transcribe"
    source = "docs"

    async def sync(self) -> SyncResult:
        if not settings.aws_access_key_id:
            raise ValueError("AWS_ACCESS_KEY_ID is not set in .env")

        stt_models = [
            SyncModel(
                model_id=model_id,
                display_name=display_name,
                type="stt",
                languages=languages,
                streaming=streaming,
                is_default=is_default,
                description=description,
            )
            for model_id, display_name, is_default, languages, streaming, description in _MODELS
        ]

        return SyncResult(
            stt_models=stt_models,
            tts_models=[],
            tts_voices=[],
            source=self.source,
            api_urls=[],
            docs_urls=_DOCS_URLS,
            notes=(
                f"{len(stt_models)} STT model tiers "
                f"({_MODELS[0][3].__len__()} batch langs, "
                f"{_MODELS[1][3].__len__()} streaming langs, "
                f"1 medical lang). STT-only."
            ),
        )


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    syncer = AmazonTranscribeSyncer()
    try:
        result = await syncer.sync()
    except ValueError as e:
        print(f"\nError: {e}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== STT Models ({len(result.stt_models)}) ===")
    for m in result.stt_models:
        marker = " [default]" if m.is_default else ""
        stream = " streaming" if m.streaming else " batch"
        print(
            f"  {m.model_id!r:35} {m.display_name!r:35} "
            f"langs={len(m.languages)}{stream}{marker}"
        )

    print(f"\nSource : {result.source}")
    print(f"Notes  : {result.notes}")
    print(f"Fetched: {result.fetched_at}")


if __name__ == "__main__":
    asyncio.run(_main())
