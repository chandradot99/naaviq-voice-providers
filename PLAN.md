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
| **PlayHT** | `mixed` | 🟡 **Next priority** |

---

## Development strategy

**No DB population until all providers are implemented.**

Each new provider may reveal schema gaps (new columns, indexes, constraints) that require migrations. Squashing migrations pre-production is cheap; migrating live data is expensive. So:

- During implementation: smoke test only (`uv run python -m naaviq.sync.<provider>`) — verifies the syncer returns valid data but never writes to DB
- Schema changes stay cheap: downgrade → edit migrations → upgrade with no data loss concern
- Once all planned providers are implemented and the schema is stable → single production sync of all providers
- This also means the admin UI sync workflow (fetch → diff → apply) is only tested end-to-end in production

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
| `mixed` | Some data in API, rest in docs | Cartesia, ElevenLabs, Google Cloud |
| `docs` | No API exposes the data — all extracted from documentation | OpenAI, Sarvam |

The `source` value is informational — it labels the dominant approach. Provenance details (URLs fetched, model used, token count) belong in `SyncResult.notes`.

---

## Azure Speech — shipped

`naaviq/sync/azure.py` — `api` source.

- TTS voices from `GET https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list`
- TTS models: `azure-neural` (default) and `azure-standard`, derived from `VoiceType` values
- STT models: two synthetic entries — `azure-stt-realtime` (streaming) and `azure-stt-batch`
- Skips non-GA voices (`Status != "GA"`)
- `compatible_models = [azure-neural | azure-standard]` per voice
- `SecondaryLocaleList` included in `languages` for multilingual voices
- Auth: `Ocp-Apim-Subscription-Key` header, `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION`

Smoke-test:
```bash
AZURE_SPEECH_KEY=... AZURE_SPEECH_REGION=eastus uv run python -m naaviq.sync.azure
```

---

## AI parser (reference — already shipped)

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

**System prompt is cached** via `cache_control: ephemeral` — multiple parses against the same docs (or within one syncer) amortize the prompt cost.

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

The registry must come before the spec. Once all providers are in, the patterns will be clear enough to draft it well.

---

## Future providers

### PlayHT (`mixed`)
- TTS models: likely AI-parsed from docs (Play3.0-mini, PlayDialog, PlayHT2.0, …)
- Voices: `GET https://api.play.ht/api/v2/voices` (if endpoint still public; otherwise docs-parsed)
- Verify auth scheme — PlayHT has rotated between user-id+secret and Bearer tokens

Follows the Cartesia/ElevenLabs `mixed` pattern — no new parser capabilities needed.
