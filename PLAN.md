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
| **PlayHT** | `mixed` | 🟡 **Next priority** |

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
| `docs` | No API exposes the data — all extracted from documentation | OpenAI, Sarvam |

The `source` value is informational — it labels the dominant approach. Provenance details (URLs fetched, model used, token count) belong in `SyncResult.notes`.

---

## Next priority: PlayHT (`mixed`)

Details in the "Future providers" section below — plan to flesh this out when we pick it up.

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

## Future providers

### PlayHT (`mixed`)
- TTS models: likely AI-parsed from docs (Play3.0-mini, PlayDialog, PlayHT2.0, …)
- Voices: `GET https://api.play.ht/api/v2/voices` (if endpoint still public; otherwise docs-parsed)
- Verify auth scheme — PlayHT has rotated between user-id+secret and Bearer tokens

Follows the Cartesia/ElevenLabs `mixed` pattern — no new parser capabilities needed.
