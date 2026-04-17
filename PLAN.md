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
| **Azure** | `api` | 🟡 **Next priority** — richest voice API, no AI-parsing needed |
| Amazon Polly | `api` or `mixed` | ⬜ After Azure (TTS only) |
| PlayHT | `mixed` | ⬜ After Polly |

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

## Next priority: Azure Speech (`api`)

Microsoft Azure AI Speech Service. Richest voice API of any provider — returns VoiceType,
Gender, Locale, StyleList, RolePlayList, WordsPerMinute, SampleRateHertz, Status per voice.
STT is locale-based with no named models (unlike Deepgram/Google).

### Source: `api`

All data comes from the voices REST API. No AI-parsing needed.

- TTS voices: `GET https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list`
- TTS models: derived from voice API `VoiceType` field (`Neural`, `Standard`)
- STT models: see open question below

### Auth

`Ocp-Apim-Subscription-Key: {key}` header. Requires an Azure AI Speech resource.
Two config values: `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION` (needed to construct the regional endpoint).

### What the voices API returns

```json
{
    "Name": "Microsoft Server Speech Text to Speech Voice (en-US, JennyNeural)",
    "DisplayName": "Jenny",
    "LocalName": "Jenny",
    "ShortName": "en-US-JennyNeural",
    "Gender": "Female",
    "Locale": "en-US",
    "LocaleName": "English (United States)",
    "StyleList": ["assistant", "chat", "customerservice", "newscast", "angry", "cheerful", ...],
    "SampleRateHertz": "24000",
    "VoiceType": "Neural",
    "Status": "GA",
    "ExtendedPropertyMap": {"IsHighQuality48K": "True"},
    "WordsPerMinute": "152"
}
```

Also has `SecondaryLocaleList` for multilingual voices and `RolePlayList` for character roles.
No pagination — returns all voices for the region in one response.

### Syncer shape

```python
class AzureSyncer(ProviderSyncer):
    provider_id = "azure"
    source = "api"

    async def sync(self) -> SyncResult:
        voices_data = await self._fetch_voices()
        tts_voices = self._parse_voices(voices_data)
        tts_models = self._derive_models(voices_data)  # from VoiceType field
        return SyncResult(
            stt_models=[],  # or synthetic entries — see open question
            tts_models=tts_models,
            tts_voices=tts_voices,
            source=self.source,
        )
```

Single API call — no AI parser needed, no concurrency needed. Fastest syncer yet.

### Voice mapping

- `voice_id` = `ShortName` (e.g., `en-US-JennyNeural`) — this is what you pass to the synthesis API
- `display_name` = `DisplayName` (e.g., `Jenny`)
- `gender` = `Gender` lowercased
- `languages` = `[Locale]` + `SecondaryLocaleList` (for multilingual voices)
- `accent` = derived from Locale region (same `_ACCENT_MAP` as Cartesia/Google)
- `meta` = `StyleList`, `RolePlayList`, `VoiceType`, `Status`, `WordsPerMinute`, `SampleRateHertz`

### TTS models (derived, not parsed)

Almost all Azure voices are `Neural` now (`Standard` is being deprecated). Derive models from unique `VoiceType` values in the voice list — likely just 2 entries: `Neural` (default) and `Standard`. Each model's languages = union of all voices with that VoiceType.

### Changes needed

#### `naaviq-voice-providers`
- `naaviq/sync/azure.py` — new file
- `naaviq/config.py` — `azure_speech_key: str = ""`, `azure_speech_region: str = ""`
- `.env.example` — `AZURE_SPEECH_KEY=`, `AZURE_SPEECH_REGION=eastus`

#### `naaviq-admin`
- `naaviq_admin/config.py` — `azure_speech_key: str = ""`, `azure_speech_region: str = ""`
- `.env.example` — `AZURE_SPEECH_KEY=`, `AZURE_SPEECH_REGION=eastus`
- `naaviq_admin/routers/providers.py` — register `"azure": "naaviq.sync.azure.AzureSyncer"` in `_SYNCERS`

### Open questions (resolve during implementation)

1. **STT models**: Azure STT has no named models — it's locale-based with 3 modes (realtime, fast, batch). Options:
   - (a) `stt_models=[]` — simplest, but hides that Azure has STT
   - (b) Synthetic entries: `azure-stt-realtime` (streaming=true), `azure-stt-batch` (streaming=false) with `languages=['*']`
   - (c) Single entry: `default` with streaming=true and meta describing all modes
   Plan leans toward (b) — gives users a meaningful choice and maps to our schema

2. **Region**: Which region to use for the voice list? All regions return the same global voice catalog. Default `eastus` is fine.

3. **Filter**: Skip custom/preview voices? Probably filter to `Status: "GA"` only, show preview voices in a separate sync or via meta flag.

### Smoke-test command

```bash
uv sync
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

## Future providers

### PlayHT (`mixed`)
- TTS models: likely AI-parsed from docs (Play3.0-mini, PlayDialog, PlayHT2.0, …)
- Voices: `GET https://api.play.ht/api/v2/voices` (if endpoint still public; otherwise docs-parsed)
- Verify auth scheme — PlayHT has rotated between user-id+secret and Bearer tokens

Follows the Cartesia/ElevenLabs `mixed` pattern — no new parser capabilities needed.
