# Naaviq — Provider Sync Implementation Plan

## Current status

| Provider | Source | Status |
|---|---|---|
| Deepgram | `api` | ✅ Shipped |
| Cartesia | `mixed` | ✅ Shipped (API voices + AI-parsed TTS/STT models) |
| ElevenLabs | `mixed` | ✅ Shipped (API TTS models + voices, AI-parsed Scribe STT) |
| OpenAI | `docs` | ✅ Shipped (AI-parsed TTS/STT models + voices) |
| Google Cloud | `mixed` | ✅ Shipped (API voices + AI-parsed TTS tiers + STT models; orphan-tier injection for legacy voices) |
| Sarvam | `docs` | ✅ Shipped (AI-parsed STT/TTS models + 44 voices; first all-Indian-language provider) |
| Azure | `api` | ✅ Shipped (API voices + derived TTS models + 2 synthetic STT entries) |
| Amazon Polly | `api` | ✅ Shipped (API voices + derived TTS models; TTS-only, no STT) |
| PlayHT | `mixed` | ⏭ Skipped (no API access on free plan) |
| Hume AI | `mixed` | ✅ Shipped (API voices + AI-parsed TTS models; TTS-only, 160 voices) |
| Inworld AI | `mixed` | ✅ Shipped (API voices + AI-parsed TTS/STT models; 135 voices, 13 locales) |
| Murf AI | `api` | ✅ Shipped (API voices + derived TTS models; TTS-only, 162 voices, 41 langs) |
| Speechmatics | `docs` | ✅ Shipped (AI-parsed STT models; STT-only, TTS skipped — preview only) |
| LMNT | `mixed` | ✅ Shipped (API voices + derived TTS models; TTS-only, 44 voices, 21 langs) |
| Rime AI | `api` | ✅ Shipped (API voices + derived TTS models; TTS-only, 404 unique voices, 4 models) |
| AssemblyAI | `docs` | ✅ Shipped (AI-parsed STT models; STT-only, 6 models) |
| Rev AI | `docs` | ✅ Shipped (AI-parsed STT models; STT-only, 3 models) |

**All planned providers are shipped. Dev DB is populated (16 providers, 116 models, 4,550 voices).**

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
| `api` | Provider exposes REST APIs for models and voices | Deepgram, Azure, Amazon Polly, Murf, LMNT, Rime |
| `mixed` | Some data in API, rest in docs | Cartesia, ElevenLabs, Google Cloud, Hume AI, Inworld AI |
| `docs` | No API — all extracted from documentation (cache or AI parser) | OpenAI, Sarvam, Speechmatics, AssemblyAI, Rev AI |

---

## AI parser cache architecture

For `docs` and `mixed` providers, `naaviq/sync/ai_parser.py` calls the Anthropic API to parse docs. When running via Claude Code (primary sync path), no `ANTHROPIC_API_KEY` is needed — Claude Code extracts the data itself and writes JSON cache files to `.sync-cache/`, which the sync scripts read transparently.

Cache files:
- `.sync-cache/{provider_id}_{model_type}_models.json` — STT or TTS models
- `.sync-cache/{provider_id}_voices.json` — TTS voices

These are gitignored (local only). Re-generate by asking Claude Code to re-extract from the docs URLs shown in the sync error output.

---

## Migrations shipped

| Migration | What |
|---|---|
| 001 | Create `providers` table |
| 002 | Create `models` table |
| 003 | Create `voices` table |
| 004 | Add `api_urls` and `docs_urls` columns to `providers` |

---

## Future initiative: Naaviq Voice Spec

Once all planned providers are implemented, extract the patterns into an open spec.

**Why**: No open standard exists for voice provider APIs. OpenAI's `/v1/audio/speech` and `/v1/audio/transcriptions` are an informal de facto standard for inference wrappers, but nothing covers voice/model discovery, capability metadata (languages, streaming, engines), or a unified runtime interface across providers.

**Two layers**:

- **Layer 1 — Registry/Discovery** (Naaviq already implements):
  `GET /v1/voices`, `/v1/models`, `/v1/providers` — standard metadata shape, filters, pagination
- **Layer 2 — Synthesis/Transcription** (runtime, what clients call):
  `POST /v1/tts/synthesize`, `POST /v1/stt/transcribe`, WebSocket streams for real-time

A provider that implements both layers is "Naaviq-compatible" — one client SDK works with all of them.

**Path**:
1. ✅ Build the registry (now — understand all provider variation first)
2. ⬜ Publish `naaviq-spec` repo — OpenAPI YAML + reference docs. Providers self-certify.
3. ⬜ Naaviq-compatible gateway — proxy that normalizes requests behind the spec

---

## AI parser (reference)

`naaviq/sync/ai_parser.py` implements an agentic Claude loop:

1. Give Claude seed URL(s) + system prompt + two tools (`fetch_url`, terminal `return_models` / `return_voices`)
2. Claude fetches pages, follows links, reads, repeats
3. Claude calls the terminal tool → we extract structured `SyncModel[]` or `SyncVoice[]`

**Model**: `claude-haiku-4-5-20251001` at `temperature=0` (deterministic); override via `NAAVIQ_AI_PARSER_MODEL`

**Safety guards**:
- `MAX_ITERATIONS=15` (with a nudge message when ≤3 remain)
- `MAX_URLS=15`
- `MAX_PAGE_CHARS=60_000` (truncated with marker; Claude follows links for more)
- Invalid terminal payload → `AIParserError` (no partial data — admin diff would deprecate everything missed)
- Friendly errors for auth / low credit / rate limit / connection failures

**Cache-first**: when `ANTHROPIC_API_KEY` is not set, `parse_models_from_docs` and `parse_voices_from_docs` check `.sync-cache/` before raising an error. This is how the Claude Code sync path works — zero API cost.
