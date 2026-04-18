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

## Hume AI — shipped

`naaviq/sync/humeai.py` — `mixed` source.

- TTS voices from `GET https://api.hume.ai/v0/tts/voices?provider=HUME_AI` (paginated, page_size=100)
- TTS models: AI-parsed from `https://dev.hume.ai/docs/text-to-speech-tts/overview`
- STT: not offered — `stt_models=[]`
- Auth: `X-Hume-Api-Key` header, `HUME_API_KEY`
- 160 voices with gender, accent, age, and language tags from API response
- `compatible_octave_models: ["1", "2"]` mapped to `["octave-1", "octave-2"]`
- Human-readable language names in tags (e.g., "English") mapped to BCP-47 via `_LANGUAGE_NAME_TO_BCP47`
- Remaining pages fetched concurrently after learning `total_pages` from page 0

Smoke-test:
```bash
HUME_API_KEY=... ANTHROPIC_API_KEY=... uv run python -m naaviq.sync.humeai
```

---

## Inworld AI — shipped

`naaviq/sync/inworld.py` — `mixed` source.

- TTS voices from `GET https://api.inworld.ai/voices/v1/voices` (paginated via `nextPageToken`)
- TTS models: AI-parsed from `https://docs.inworld.ai/tts/tts`
- STT models: AI-parsed from `https://docs.inworld.ai/stt/overview`
- Note: `GET /llm/v1alpha/models` exists but returns LLM Router models, not TTS/STT
- Auth: `Authorization: Basic <base64_key>`, `INWORLD_API_KEY`
- 135 voices across 13 locales (en-US, zh-CN, nl-NL, fr-FR, de-DE, it-IT, ja-JP, ko-KR, pl-PL, pt-BR, es-ES, ru-RU, hi-IN, he-IL, ar-SA)
- Voice fields: `gender`, `ageGroup`, `langCode` (e.g., `EN_US` → normalized to `en-US`), `tags`, `categories`, `description`
- Accent derived from tags array (e.g., `["british", "eloquent"]` → `accent="british"`)
- `compatible_models = []` — all voices work with all TTS models
- Only `source: "SYSTEM"` voices synced (premade); user-cloned voices skipped

Smoke-test:
```bash
INWORLD_API_KEY=... ANTHROPIC_API_KEY=... uv run python -m naaviq.sync.inworld
```
