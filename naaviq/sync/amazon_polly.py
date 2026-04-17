"""
Amazon Polly sync script.

Source: GET https://polly.{region}.amazonaws.com/v1/voices (API)
  - TTS voices only — Polly is a TTS-only service (stt_models=[])
  - TTS models derived from unique SupportedEngines values in the voice list
  - Paginated via NextToken — all pages are fetched and merged

Voice API fields used:
  Id                    → voice_id  (e.g., "Joanna", "Matthew")
  Name                  → display_name
  Gender                → gender
  LanguageCode          → primary language
  AdditionalLanguageCodes → secondary languages for bilingual voices
  SupportedEngines      → ["neural", "standard", "long-form", "generative"]
                          → compatible_models via _ENGINE_TO_MODEL

Auth: AWS Signature V4 using AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY + AWS_REGION.
Signing is implemented with stdlib (hmac + hashlib) — no boto3 needed.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from naaviq.config import settings
from naaviq.sync.base import HTTP_TIMEOUT, ProviderSyncer, SyncModel, SyncResult, SyncVoice
from naaviq.sync.language import ACCENT_MAP, normalize_languages

_SERVICE = "polly"
_VOICES_PATH = "/v1/voices"

# Engine name → our model_id
_ENGINE_TO_MODEL: dict[str, str] = {
    "neural":    "polly-neural",
    "standard":  "polly-standard",
    "long-form": "polly-long-form",
    "generative": "polly-generative",
}

# Engine name → display name
_ENGINE_DISPLAY: dict[str, str] = {
    "neural":    "Polly Neural",
    "standard":  "Polly Standard",
    "long-form": "Polly Long-Form",
    "generative": "Polly Generative",
}

# Model priority for is_default — generative > neural > long-form > standard
_DEFAULT_PRIORITY = ["generative", "neural", "long-form", "standard"]


class AmazonPollySyncer(ProviderSyncer):
    provider_id = "amazon-polly"
    source = "api"

    async def sync(self) -> SyncResult:
        voices_data = await self._fetch_all_voices()
        tts_voices = self._parse_voices(voices_data)
        tts_models = self._derive_tts_models(voices_data)
        return SyncResult(
            stt_models=[],
            tts_models=tts_models,
            tts_voices=tts_voices,
            source=self.source,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch_all_voices(self) -> list[dict]:
        if not settings.aws_access_key_id:
            raise ValueError("AWS_ACCESS_KEY_ID is not set in .env")
        if not settings.aws_secret_access_key:
            raise ValueError("AWS_SECRET_ACCESS_KEY is not set in .env")

        region = settings.aws_region or "us-east-1"
        host = f"polly.{region}.amazonaws.com"
        all_voices: list[dict] = []
        next_token: str | None = None

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            while True:
                params: dict[str, str] = {}
                if next_token:
                    params["NextToken"] = next_token

                headers = _sigv4_headers(
                    method="GET",
                    host=host,
                    path=_VOICES_PATH,
                    params=params,
                    region=region,
                    access_key=settings.aws_access_key_id,
                    secret_key=settings.aws_secret_access_key,
                )

                url = f"https://{host}{_VOICES_PATH}"
                if params:
                    url += "?" + urlencode(params)

                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                body = resp.json()

                all_voices.extend(body.get("Voices", []))

                next_token = body.get("NextToken")
                if not next_token:
                    break

        return all_voices

    def _parse_voices(self, voices_data: list[dict]) -> list[SyncVoice]:
        voices: list[SyncVoice] = []

        for v in voices_data:
            primary = v.get("LanguageCode", "")
            additional = v.get("AdditionalLanguageCodes") or []
            all_locales = [primary] + additional
            languages = normalize_languages([loc for loc in all_locales if loc])

            supported_engines = v.get("SupportedEngines") or []
            compatible_models = [
                _ENGINE_TO_MODEL[e]
                for e in supported_engines
                if e in _ENGINE_TO_MODEL
            ]

            accent = _accent_from_locale(primary)

            voices.append(SyncVoice(
                voice_id=v["Id"],
                display_name=v.get("Name", v["Id"]),
                gender=v.get("Gender", "").lower() or None,
                category="premade",
                languages=languages,
                accent=accent,
                compatible_models=compatible_models,
                meta={
                    "language_name": v.get("LanguageName"),
                    "supported_engines": supported_engines,
                },
            ))

        return voices

    def _derive_tts_models(self, voices_data: list[dict]) -> list[SyncModel]:
        """Build one TTS model per unique engine found across all voices."""
        engine_langs: dict[str, set[str]] = {}

        for v in voices_data:
            primary = v.get("LanguageCode", "")
            if not primary:
                continue
            for engine in v.get("SupportedEngines") or []:
                if engine not in _ENGINE_TO_MODEL:
                    continue
                if engine not in engine_langs:
                    engine_langs[engine] = set()
                engine_langs[engine].update(normalize_languages([primary]))

        # Determine which engine is the default (highest priority present)
        default_engine = next(
            (e for e in _DEFAULT_PRIORITY if e in engine_langs),
            None,
        )

        models: list[SyncModel] = []
        for engine, langs in engine_langs.items():
            models.append(SyncModel(
                model_id=_ENGINE_TO_MODEL[engine],
                display_name=_ENGINE_DISPLAY[engine],
                type="tts",
                languages=sorted(langs),
                streaming=True,
                is_default=(engine == default_engine),
                meta={"engine": engine},
            ))

        # Sort by priority order for consistent output
        priority_index = {e: i for i, e in enumerate(_DEFAULT_PRIORITY)}
        models.sort(key=lambda m: priority_index.get(m.meta.get("engine", ""), 99))
        return models


# ── AWS Signature V4 signing ──────────────────────────────────────────────────

def _sigv4_headers(
    method: str,
    host: str,
    path: str,
    params: dict[str, str],
    region: str,
    access_key: str,
    secret_key: str,
) -> dict[str, str]:
    """Build Authorization + x-amz-date headers for a GET request using SigV4."""
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    canonical_querystring = urlencode(sorted(params.items()))
    canonical_headers = f"host:{host}\nx-amz-date:{amz_date}\n"
    signed_headers = "host;x-amz-date"
    payload_hash = hashlib.sha256(b"").hexdigest()

    canonical_request = "\n".join([
        method,
        path,
        canonical_querystring,
        canonical_headers,
        signed_headers,
        payload_hash,
    ])

    credential_scope = f"{date_stamp}/{region}/{_SERVICE}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        credential_scope,
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])

    signing_key = _get_signing_key(secret_key, date_stamp, region, _SERVICE)
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    return {
        "x-amz-date": amz_date,
        "Authorization": authorization,
    }


def _get_signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    k_date    = _hmac_sha256(f"AWS4{secret_key}".encode(), date_stamp)
    k_region  = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, service)
    return _hmac_sha256(k_service, "aws4_request")


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _accent_from_locale(locale: str) -> str | None:
    """Derive accent from BCP-47 locale. e.g., 'en-GB' → 'british'."""
    parts = locale.split("-")
    if len(parts) >= 2:
        return ACCENT_MAP.get(parts[1].upper())
    return None


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    import sys

    syncer = AmazonPollySyncer()
    try:
        result = await syncer.sync()
    except ValueError as e:
        print(f"\nError: {e}", file=sys.stderr)
        raise SystemExit(1)
    except httpx.HTTPStatusError as e:
        print(f"\nPolly API error ({e.response.status_code}): {e.response.text[:300]}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== TTS Models ({len(result.tts_models)}) ===")
    for m in result.tts_models:
        print(
            f"  {m.model_id!r:25} {m.display_name!r:22} "
            f"langs={len(m.languages)} is_default={m.is_default}"
        )

    print(f"\n=== TTS Voices ({len(result.tts_voices)}) — showing first 20 ===")
    for v in result.tts_voices[:20]:
        print(
            f"  {v.voice_id!r:15} {v.display_name!r:15} "
            f"gender={v.gender or '?':6} langs={v.languages} "
            f"models={v.compatible_models}"
        )
    if len(result.tts_voices) > 20:
        print(f"  ... and {len(result.tts_voices) - 20} more")

    print(f"\nSource: {result.source}")
    print(f"Fetched at: {result.fetched_at}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(_main())
