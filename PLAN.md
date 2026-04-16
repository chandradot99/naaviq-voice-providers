# Naaviq — Provider Sync Implementation Plan

## Current status

| Provider | Source | Status |
|---|---|---|
| Deepgram | `api` | ✅ Shipped |
| Cartesia | `mixed` | ✅ Shipped (API voices + AI-parsed TTS/STT models) |
| ElevenLabs | `mixed` | ✅ Shipped (API TTS models + voices, AI-parsed Scribe STT) |
| **OpenAI** | `docs` | 🟡 **Next priority** — syncer written, ready to smoke test |
| Sarvam | `docs` | ⏸️ Blocked — `docs.sarvam.ai` TLS cert failure (confirmed 2026-04-17, site unreachable in browser too). Resume when docs return. |
| PlayHT | `mixed` | ⬜ After OpenAI |

---

## Architecture recap

| Repo | Role |
|---|---|
| `naaviq-voice-providers` | Public. ORM models, Alembic migrations, public read API, sync scripts |
| `naaviq-admin` | Private. Admin write API — triggers sync, diffs, applies to DB |
| `naaviq-admin-ui` | Private. Admin frontend — fetch → diff → apply workflow |

## Source types

| Type | When to use | Current examples |
|---|---|---|
| `api` | Provider exposes REST APIs for both models and voices | Deepgram |
| `mixed` | Some data in API, rest in docs | Cartesia, ElevenLabs |
| `docs` | No API exposes the data — all extracted from documentation | Sarvam (planned) |

The `source` value is informational — it labels the dominant approach. Provenance details (URLs fetched, model used, token count) belong in `SyncResult.notes`.

---

## Next priority: OpenAI ✅ syncer written

Source changed to `docs` — `developers.openai.com` is publicly accessible and cleanly categorizes TTS/STT models. No API key needed for the sync itself. No hardcoding anywhere.

### Seed URLs

- Models (TTS + STT): `https://developers.openai.com/api/docs/models/all`
- Voices: `https://developers.openai.com/api/docs/guides/text-to-speech`

### Why `docs` not `mixed`

The `/v1/models` API returns all model types (chat, image, embeddings, realtime, audio) with zero type metadata — filtering by ID pattern is fragile. The docs page categorizes TTS vs STT explicitly. Voices have no API endpoint. So all data comes from docs.

### What's in the docs (verified 2026-04-17)

**TTS models (3):** `tts-1`, `tts-1-hd`, `gpt-4o-mini-tts` (default)

**STT models (4):** `whisper-1`, `gpt-4o-transcribe` (default), `gpt-4o-mini-transcribe`, `gpt-4o-transcribe-diarize`

**Voices (13):** alloy, ash, ballad, coral, echo, fable, nova, onyx, sage, shimmer, verse, marin, cedar
- ballad, verse, marin, cedar — `gpt-4o-mini-tts` only (not supported by tts-1/tts-1-hd)
- marin, cedar — recommended for best quality

### Syncer shape (3 parallel calls)

```python
class OpenAISyncer(ProviderSyncer):
    provider_id = "openai"
    source = "docs"

    async def sync(self) -> SyncResult:
        (tts_models, _), (stt_models, _), (tts_voices, _) = await asyncio.gather(
            parse_models_from_docs(seed_urls=_MODELS_DOCS, model_type="tts", guidance=_TTS_GUIDANCE),
            parse_models_from_docs(seed_urls=_MODELS_DOCS, model_type="stt", guidance=_STT_GUIDANCE),
            parse_voices_from_docs(seed_urls=_VOICES_DOCS, guidance=_VOICES_GUIDANCE),
        )
        ...
```

TTS + STT share the same seed URL → prompt caching amortizes cost.

### Changes needed (all done ✅)

#### `naaviq-voice-providers`
- ✅ `naaviq/sync/openai.py` — created
- ✅ `naaviq/config.py` — `openai_api_key: str = ""`
- ✅ `.env.example` — `OPENAI_API_KEY=`

#### `naaviq-admin`
- ✅ `naaviq_admin/config.py` — `openai_api_key: str = ""`
- ✅ `.env.example` — `OPENAI_API_KEY=`
- ✅ `naaviq_admin/routers/providers.py` — `"openai": "naaviq.sync.openai.OpenAISyncer"` registered

### Smoke-test command

```bash
uv sync --extra sync
ANTHROPIC_API_KEY=... uv run python -m naaviq.sync.openai
```

---

## Blocked: Sarvam

**Status:** ⏸️ Blocked on `docs.sarvam.ai` TLS cert failure (confirmed 2026-04-17). Both WebFetch and browser hit "unknown certificate verification error" — `sarvam.ai` main domain loads fine, issue is specific to the docs subdomain. Not an IP block (that'd be HTTP 403, not a TLS error). Resume once cert is fixed.

**Prep already done:**
- `naaviq/sync/ai_parser.py` — `_run_agentic_loop` factored out, `parse_voices_from_docs` + `return_voices` terminal tool shipped, `_to_sync_voice` helper added. Ready to call when docs return.

---

### Original plan (preserved for when docs return)

Indian AI provider specializing in Indian languages. Both STT (Saaras family) and TTS (Bulbul family). Sarvam is the first provider where **voices also need to be parsed from docs** — there's no public voices API. This will force one extension to the AI parser.

### Why Sarvam next

- Adds first-class coverage for Indian regional languages — ta-IN, bn-IN, te-IN, kn-IN, ml-IN, mr-IN, gu-IN, pa-IN, od-IN — none of which are served by existing providers (ElevenLabs covers Hindi, but not these)
- Validates the `docs`-only source path end-to-end (Cartesia and ElevenLabs are `mixed` — they still have API fallbacks)
- Exercises the new docs-parsed-voices flow (see §"AI parser extension" below)

### What's known

- STT models: Saaras family (saaras:v1, saaras:v2, saaras:v2.5, saarika — details in docs)
- TTS models: Bulbul family (bulbul:v1, bulbul:v2 — verify current state)
- TTS voices: named speakers (e.g., "meera", "pavithra", "arvind", "amol") — enumerated in the TTS API's `speaker` parameter documentation, no dedicated voices endpoint
- Auth: `api-subscription-key` header
- Docs host: `docs.sarvam.ai`

### Seed URLs (verify at implementation time)

Sarvam reorganizes docs occasionally — confirm these resolve before committing:

- TTS models + voices: `https://docs.sarvam.ai/api-reference-docs/text-to-speech/convert` (and possibly a sibling models overview page)
- STT models: `https://docs.sarvam.ai/api-reference-docs/speech-to-text/transcribe`

If the seed pages don't enumerate everything, the parser will follow links — same pattern as Cartesia.

### AI parser extension (✅ shipped ahead of Sarvam)

Decision was Option A: sibling `parse_voices_from_docs()` function with a `return_voices` terminal tool, sharing all agentic-loop scaffolding with `parse_models_from_docs()`. Implementation already merged in `naaviq/sync/ai_parser.py` — see `_run_agentic_loop` (private), `_to_sync_voice` (private), and `parse_voices_from_docs` (public). Sarvam sync just needs to call it.

### `sarvam.py` syncer shape

```python
class SarvamSyncer(ProviderSyncer):
    provider_id = "sarvam"
    source = "docs"

    async def sync(self) -> SyncResult:
        ai_key = settings.anthropic_api_key or None

        (stt_models, stt_notes), (tts_models, tts_notes), (tts_voices, voice_notes) = await asyncio.gather(
            parse_models_from_docs(seed_urls=_STT_DOCS, ..., model_type="stt", guidance=_STT_GUIDANCE),
            parse_models_from_docs(seed_urls=_TTS_DOCS, ..., model_type="tts", guidance=_TTS_GUIDANCE),
            parse_voices_from_docs(seed_urls=_TTS_DOCS, ..., guidance=_VOICES_GUIDANCE),
        )
        return SyncResult(stt_models=stt_models, tts_models=tts_models, tts_voices=tts_voices, source="docs", notes=...)
```

Three parallel parser calls vs sequential → latency stays ~10s instead of ~30s. Note all three likely share TTS docs URLs, so prompt caching should kick in across calls.

### Guidance strings (drafts)

- **STT**: "Sarvam's STT product is the Saaras family. Return all listed models (saaras:v1, :v2, :v2.5, saarika, etc.). Mark the latest production-recommended model as is_default=true. Languages should be BCP-47 with uppercase region (hi-IN, ta-IN, …). Populate meta with diarization, word_timestamps, max_audio_duration, realtime if the docs mention them."
- **TTS**: "Sarvam's TTS product is the Bulbul family. Return all listed models (bulbul:v1, bulbul:v2). Mark the latest as is_default=true. Same language format. Meta should include supported sample rates, max input characters, pace/pitch controls if documented."
- **Voices**: "Return every speaker listed under the TTS speaker parameter (meera, pavithra, arvind, amol, maya, …). Infer gender from the docs (if unlabeled, leave gender=null). Languages = the BCP-47 codes that speaker supports. category='premade'. Omit preview_url unless a direct audio sample URL is in the docs. Don't invent ages or accents."

### Changes needed

#### `naaviq-voice-providers`
- ~~`naaviq/sync/ai_parser.py` — factor agentic loop into `_run_agentic_loop(...)`; add `parse_voices_from_docs(...)` with a `return_voices` terminal tool~~ ✅ done
- `naaviq/sync/sarvam.py` — new file (per shape above)
- `naaviq/config.py` — `sarvam_api_key: str = ""` (currently unused by the syncer but set aside for future endpoints)
- `.env.example` — add `SARVAM_API_KEY=`
- `README.md` / `CLAUDE.md` — add Sarvam to the source-type table

#### `naaviq-admin`
- `naaviq_admin/config.py` — `sarvam_api_key: str = ""`
- `.env.example` — add `SARVAM_API_KEY=`
- `naaviq_admin/routers/providers.py` — register `"sarvam": "naaviq.sync.sarvam.SarvamSyncer"` in `_SYNCERS`

### Open questions (resolve during implementation)

- Does Sarvam expose a public voices JSON anywhere? If yes, prefer API → re-classify as `mixed`
- Are preview audio URLs published in docs? If not, `preview_url=None` for all voices
- Does Saaras have streaming/realtime variants we should flag in `meta.realtime`?
- Confirm `api-subscription-key` vs `Authorization: Bearer` header — docs have shifted here

### Smoke-test command

```bash
uv sync --extra sync
ANTHROPIC_API_KEY=... uv run python -m naaviq.sync.sarvam
```

---

## AI parser (reference — already shipped)

`naaviq/sync/ai_parser.py` implements an agentic Claude loop:

1. Give Claude seed URL(s) + system prompt + two tools (`fetch_url`, terminal `return_models` / `return_voices`)
2. Claude fetches pages, follows links, reads, repeats
3. Claude calls the terminal tool → we extract structured `SyncModel[]` or `SyncVoice[]`

**Model**: `claude-sonnet-4-6` at `temperature=0` (deterministic)

**Safety guards**:
- `MAX_ITERATIONS=15` (with a nudge message when ≤3 remain)
- `MAX_URLS=15`
- `MAX_PAGE_CHARS=60_000` (truncated with marker; Claude follows links for more)
- Invalid terminal payload → `AIParserError` (no partial data — admin diff would deprecate everything missed)
- Friendly errors for auth / low credit / rate limit / connection failures

**System prompt is cached** via `cache_control: ephemeral` — multiple parses against the same docs (or within one syncer) amortize the prompt cost.

---

## Future providers

### PlayHT (`mixed`)
- TTS models: likely AI-parsed from docs (Play3.0-mini, PlayDialog, PlayHT2.0, …)
- Voices: `GET https://api.play.ht/api/v2/voices` (if endpoint still public; otherwise docs-parsed)
- Verify auth scheme — PlayHT has rotated between user-id+secret and Bearer tokens

Follows the Cartesia/ElevenLabs `mixed` pattern — no new parser capabilities needed.
