# naaviq-voice-providers

Open-source voice provider registry. A public read-only REST API that serves metadata about STT/TTS providers — models, voices, languages, and capabilities.

## What this is

- **Not a Python package** — this is a FastAPI application, not published to PyPI
- **Public API** at `providers.naaviq.ai` — no auth, rate limited at 100 req/min/IP
- **Data source of truth** for voice provider metadata used by Vaaniq and the broader community

## The 3-repo architecture

| Repo | Visibility | Purpose |
|---|---|---|
| `naaviq-voice-providers` (this repo) | Public | Read-only registry API + sync scripts |
| `naaviq-admin` | Private | Admin API — triggers sync, applies diffs, manages DB |
| `naaviq-admin-ui` | Private | Admin frontend — sync button, diff review UI |

### How it all fits together

The sync scripts (`naaviq/sync/*.py`) are the single source of truth for fetching provider data. Two paths can orchestrate the fetch→diff→apply — both produce identical results:

**Path A — Claude Code (primary, zero per-token cost):**
1. Run `uv run python scripts/sync.py [provider]` — fetches live data, shows diff, applies to dev DB
2. Review changes in dev DB
3. Run `uv run python scripts/promote.py` — copies dev → prod (no AI parsing, pure data copy)

**Path B — Admin UI (alternative, when visual diff review is preferred):**
1. **Community** contributes sync scripts for new providers via PRs to this repo (`naaviq/sync/`)
2. **Your team** reviews + merges the PR
3. **Your team** clicks "Fetch" in `naaviq-admin-ui` → calls `naaviq-admin` API
4. **`naaviq-admin`** imports and runs the sync script → fetches live data → returns the result for review (no DB write)
5. **Your team** reviews the diff in admin UI (new models/voices added, deprecated ones flagged)
6. **Your team** clicks "Apply" → `naaviq-admin` upserts into the shared DB

Both paths use the same `naaviq/sync/*.py` scripts — swapping between them produces identical DB state.

### Why sync logic lives here but apply lives in naaviq-admin

- Sync script logic is open-source — community can read, audit, and contribute
- The API endpoint that *triggers* sync and *writes* to DB is private — only your team can run it
- Zero write surface in this public repo → no attack vector

## Stack

- Python 3.12, FastAPI, SQLAlchemy (async), PostgreSQL, Alembic, slowapi
- `uv` for dependency management
- Postgres runs in Docker (local dev), deployed separately in prod
- Optional `[sync]` extra installs `anthropic` SDK for AI-doc-parser providers

## Project structure

```
naaviq-voice-providers/
├── naaviq/
│   ├── main.py           — FastAPI app entry point
│   ├── config.py         — settings (DATABASE_URL, provider keys, ANTHROPIC_API_KEY)
│   ├── db.py             — async SQLAlchemy engine + get_db
│   ├── models.py         — ORM: Provider, Model, Voice
│   ├── schemas.py        — Pydantic response schemas
│   ├── limiter.py        — slowapi rate limiter
│   ├── routers/
│   │   ├── providers.py  — GET /v1/providers, /v1/providers/{id}/models, /voices
│   │   └── catalog.py    — GET /v1/models, /v1/voices (cross-provider)
│   └── sync/
│       ├── base.py       — SyncResult contract + ProviderSyncer base class
│       ├── registry.py   — single source of truth: SyncerEntry list of all providers
│       ├── language.py   — BCP-47 normalization + ACCENT_MAP
│       ├── ai_parser.py  — Agentic Claude loop for parsing models/voices from docs
│       ├── cache.py      — JSON cache for AI-extracted data (.sync-cache/ dir, gitignored)
│       ├── deepgram.py        — Deepgram (api: both)
│       ├── cartesia.py        — Cartesia (mixed: both)
│       ├── elevenlabs.py      — ElevenLabs (mixed: both)
│       ├── openai.py          — OpenAI (docs: both)
│       ├── google_cloud.py    — Google Cloud (mixed: both)
│       ├── sarvam.py          — Sarvam (docs: both)
│       ├── azure.py           — Azure Speech (api: both)
│       ├── amazon_polly.py    — Amazon Polly (api: tts)
│       ├── humeai.py          — Hume AI (mixed: tts)
│       ├── inworld.py         — Inworld AI (mixed: both)
│       ├── murf.py            — Murf AI (api: tts)
│       ├── speechmatics.py    — Speechmatics (docs: stt)
│       ├── lmnt.py            — LMNT (mixed: tts)
│       ├── rime.py            — Rime AI (api: tts)
│       ├── assemblyai.py      — AssemblyAI (docs: stt)
│       ├── revai.py           — Rev AI (docs: stt)
│       ├── gladia.py          — Gladia (docs: stt)
│       ├── minimax.py         — MiniMax (mixed: tts)
│       ├── ibm.py             — IBM Watson (api: both)
│       ├── neuphonic.py       — Neuphonic (api: tts)
│       ├── amazon_transcribe.py — Amazon Transcribe (docs: stt)
│       ├── resemble.py        — Resemble AI (mixed: tts)
│       ├── fishaudio.py       — Fish Audio (mixed: both)
│       ├── unrealspeech.py    — Unreal Speech (docs: tts)
│       ├── smallestai.py      — Smallest AI (mixed: both)
│       ├── lovoai.py          — Lovo AI (mixed: tts)
│       ├── mistral.py         — Mistral AI (mixed: both)
│       ├── wellsaid.py        — WellSaid Labs (mixed: tts)
│       ├── cambai.py          — CAMB.ai (mixed: both)
│       ├── speechify.py       — Speechify (mixed: tts)
│       ├── typecastai.py      — Typecast AI (mixed: tts)
│       └── groq.py            — Groq (mixed: both)
├── scripts/
│   ├── sync.py           — run syncers, diff vs dev DB, apply to dev DB
│   └── promote.py        — copy dev DB state → prod DB (zero token cost)
├── alembic/              — DB migrations (001 providers, 002 models, 003 voices, 004 provider source urls)
├── tests/
├── docker-compose.yml    — Postgres only (for local dev)
└── CLAUDE.md
```

## Database tables

- **`providers`** — Cartesia, ElevenLabs, OpenAI, Deepgram, Sarvam, etc. Includes `api_urls` and `docs_urls` arrays so consumers can trace where the data came from.
- **`models`** — STT/TTS models per provider (languages, streaming, is_default)
- **`voices`** — TTS voices per provider (gender, category, languages, compatible_models, preview_url)

All tables use `deprecated_at` instead of hard deletes. Sync scripts never write to DB.

## Sync scripts

### Three source types

| Type | When to use | Example providers |
|---|---|---|
| `api` | Provider exposes a REST API for both models and voices | Deepgram, Azure, Amazon Polly |
| `docs` | No API — parse documentation with AI | OpenAI, Sarvam |
| `mixed` | API for some data (e.g., voices) + docs parsing for the rest (e.g., models) | Cartesia, ElevenLabs, Hume AI, Inworld AI |

### The AI parser (`naaviq/sync/ai_parser.py`)

Agentic Claude loop for extracting structured models from documentation pages. Used by `docs` and `mixed` sources.

```python
from naaviq.sync.ai_parser import parse_models_from_docs

models, notes = await parse_models_from_docs(
    seed_urls=["https://docs.cartesia.ai/build-with-cartesia/tts-models/latest"],
    provider_id="cartesia",
    model_type="tts",
    guidance="Mark the newest family root as is_default. Populate meta with snapshot_date, eol_date, …",
)
```

Two tools exposed to Claude:
- `fetch_url(url)` — returns plain text from HTML pages
- `return_models(models)` — terminal, JSONSchema-validated, called exactly once

Safety guards: `MAX_ITERATIONS=15`, `MAX_URLS=15`, `MAX_PAGE_CHARS=60_000`. Runs at `temperature=0` for deterministic output. System prompt is cached via `cache_control: ephemeral`. When ≤3 iterations remain, a nudge text block is appended to tool results telling Claude to call `return_models` immediately.

Failure modes raise `AIParserError` with a friendly message — auth errors, low credit balance, rate limits, hitting the iteration cap, etc.

### Language normalization (`naaviq/sync/language.py`)

Every provider uses a different language format. All languages are normalized to **BCP-47 with uppercase region** before storing:

| Provider | Their format | Normalized |
|---|---|---|
| Deepgram | `"en-us"`, `"fr-fr"` | `"en-US"`, `"fr-FR"` |
| Cartesia | `"en"`, `"fr"` | `"en"`, `"fr"` |
| ElevenLabs | `"en"`, `"hu"` | `"en"`, `"hu"` |
| Multilingual catch-all | (provider says "supports many") | `"*"` |
| Sarvam | `"hi-IN"`, `"en-IN"` | already correct |
| Hume AI | `"English"`, `"Japanese"` | `"en"`, `"ja"` (mapped via `_LANGUAGE_NAME_TO_BCP47`) |
| Inworld AI | `"EN_US"`, `"zh_CN"` | `"en-US"`, `"zh-CN"` (underscore → hyphen) |
| Rime AI | `"eng"`, `"ger"`, `"por"` | `"en"`, `"de"`, `"pt"` (ISO 639-2 → BCP-47 via `_ISO3_TO_BCP47`) |
| LMNT | `"ar"`, `"zh"` | `"ar"`, `"zh"` (already ISO 639-1, pass through `normalize_languages`) |

`"*"` is the wildcard for "supports many languages, no enumerated list" (e.g., ElevenLabs Scribe's ~99 languages). Always call `normalize_languages(langs)` before returning from any fetch method.

### Voice-model relationship (`compatible_models`)

`SyncVoice.compatible_models: list[str]` maps each voice to the TTS model(s) it works with. Convention:
- `["*"]` — voice works with **all** models for that provider (wildcard, mirrors the `"*"` convention for `languages`). Use this for providers like Cartesia where voices are model-agnostic.
- `["aura-2"]`, `["Chirp3-HD"]`, … — restricted to explicit model IDs (e.g., Deepgram, Google Cloud).
- `[]` — **unknown/unmapped** (fallback case). Voice won't appear when filtering by `?model=…`. Avoid when possible — prefer explicit `["*"]` if the provider doesn't restrict.

Stored as a Postgres `ARRAY(String)` column on the `voices` table. Model-filter queries use `compatible_models @> ARRAY[:model] OR compatible_models @> ARRAY['*']`.

### The `meta` field

Both `SyncModel` and `SyncVoice` have a `meta: dict` for provider-specific data that doesn't belong in the core schema. Stored as JSONB. Used for capability flags, snapshot/EOL dates, latency hints, etc.

### The `regions` field (models only)

A GIN-indexed `text[]` column capturing deployment regions. Canonical vocab: `us`, `eu`, `asia`, `global` (lowercase, controlled — not freeform).

Conventions (mirror the `compatible_models` wildcard pattern):

- `[]` — unknown / not documented. Filter queries **won't** match these rows.
- `["global"]` — available worldwide / no regional restriction.
- `["us","eu"]` — restricted to the listed regions.

The `?region=<x>` filter matches rows that contain `<x>` **OR** contain `"global"`. So a model with `regions=["global"]` shows up in every region filter; a model with `regions=["us"]` shows up only when `?region=us`; a model with `[]` never matches any region filter.

AI parser only emits canonical vocab values. It never fabricates `"global"` as a fallback — if regions aren't documented, the field is omitted and stays `[]` (unknown).

### `Provider.type` is derived from models on every sync

`Provider.type` (`stt` | `tts` | `both`) is seeded from the registry when a provider row is first created, but the **truth** is the set of active models for that provider. After every sync apply (both `scripts/sync.py` and the admin `/apply` endpoint), `derive_provider_type()` recomputes the effective type from non-deprecated models and updates `Provider.type` if it drifted. Self-healing: if a TTS-only provider ships an STT model (or vice versa), the next sync flips the column without manual intervention. When a provider has no active models (e.g., immediately after creation), the seeded value is left alone.

### The `lifecycle` field (models only)

Tracks product stage — one of `alpha`, `beta`, `ga`, `deprecated`. Default `ga`.

Syncers emit **only** `alpha | beta | ga`. The `deprecated` value is reserved for the admin apply logic's stale sweep: when an incoming SyncResult omits a previously known model, the apply code sets `deprecated_at=now()` **and** `lifecycle='deprecated'` together.

A DB CHECK enforces `(lifecycle = 'deprecated') = (deprecated_at IS NOT NULL)` — the two fields are always in lockstep. On un-deprecation (a model that was stale reappears in a sync), the incoming payload's `lifecycle` (default `ga`) overwrites the row's value and `deprecated_at` is cleared in the same transaction.

When to emit `alpha` / `beta` from a syncer: when the provider's docs explicitly label a model as "Preview," "Experimental," "Beta," etc. Don't infer. AI parser guidance makes this explicit.

### The `capabilities` field (models and voices)

A queryable `text[]` column on both `models` and `voices`. Stores **canonical** capability flags drawn from a controlled vocabulary — NOT freeform. Unknown or provider-specific features stay in `meta`.

**STT model vocab:** `word_timestamps`, `speaker_diarization`, `punctuation`, `profanity_filter`, `custom_vocabulary`, `language_detection`, `translation`, `sentiment`, `pii_redaction`, `summarization`, `topic_detection`

**TTS model vocab:** `emotion`, `voice_cloning`, `voice_design`, `ssml`, `phoneme_input`, `prosody_control`, `style_control`, `multi_speaker`

**Voice vocab:** `emotion`, `multilingual_native` — most capabilities live on the model; a voice flag is only set when a feature is opt-in per voice (e.g., only certain Cartesia voices support emotion even though the model does).

GIN-indexed on both tables. Consumers filter with `?capabilities=emotion,ssml` (match ALL) — route logic uses `capabilities @> ARRAY[...]`.

Extending the vocabulary is a deliberate schema change: add the new term here, update the AI parser prompt, and communicate it in release notes. Never let the AI parser invent terms — if the canonical list doesn't fit, the feature goes in `meta`.

### Audio capability fields (models only)

Four columns on `models` describe audio I/O. The semantics flip with `type`:

| Field | TTS (output side) | STT (input side) |
|---|---|---|
| `sample_rates_hz: int[]` | Output sample rates the model can produce | Input sample rates the model accepts |
| `audio_formats: text[]` | Output formats produced (lowercased: `mp3`, `wav`, `pcm`, `opus`, `flac`, `ogg`, …) | Input formats accepted |
| `max_text_chars: int \| null` | Max characters per request | — (leave NULL) |
| `max_audio_seconds: int \| null` | — (leave NULL) | Max audio duration per request |

Defaults: empty array / NULL = "unknown, check provider docs." AI parser populates these from each provider's docs; never guess a number that isn't explicitly documented.

### The `pricing` field (models only)

`SyncModel.pricing: dict` captures per-model pricing as structured JSONB. `{}` means "not recorded — check the provider's pricing page." Populate only when an explicit numeric price is on the docs/pricing page; never guess.

Shape:

```jsonc
{
  "unit":       "character" | "minute" | "second" | "word" | "token" | "request" | "hour",
  "price_usd":  0.00003,                                      // cost per unit in USD
  "free_quota": {"amount": 10000, "unit": "character", "period": "month"},   // optional
  "variants":   [                                             // optional — overrides per tier/mode
    {"applies_to": "streaming", "unit": "minute", "price_usd": 0.0043}
  ],
  "as_of":      "2026-04-19",                                 // capture date
  "source_url": "https://provider.com/pricing",
  "notes":      "Bulk tier below 1M chars/month"              // optional
}
```

Not stored on voices — TTS providers price per-model, not per-voice. Pricing is extracted by the AI parser from each provider's docs/pricing page when available.

### The sync contract (`naaviq/sync/base.py`)

Each sync script implements one method — `sync()` — returning `SyncResult(stt_models, tts_models, tts_voices, source, fetched_at, notes)`. Internal helpers are private.

```python
class MyProviderSyncer(ProviderSyncer):
    provider_id = "myprovider"
    source = "mixed"  # "api" | "docs" | "mixed"

    async def sync(self) -> SyncResult:
        # Run independent fetches in parallel for speed
        models_data, voices_data, (stt_models, _) = await asyncio.gather(
            self._fetch_models_api(),
            self._fetch_voices_api(),
            parse_models_from_docs(seed_urls=[…], provider_id=self.provider_id, model_type="stt"),
        )
        return SyncResult(
            stt_models=stt_models,
            tts_models=self._parse_models(models_data),
            tts_voices=self._parse_voices(voices_data),
            source=self.source,
        )
```

`naaviq-admin` diffs `stt_models`, `tts_models`, and `tts_voices` independently against the DB before applying.

### Adding a new provider

1. Create `naaviq/sync/{provider_id}.py`
2. Subclass `ProviderSyncer`, set `provider_id` and `source`
3. Implement `sync()` — return empty lists for unsupported types (e.g., `stt_models=[]` for TTS-only providers)
4. Use `normalize_languages()` on all language lists
5. Put provider-specific extras in `meta`
6. For `api` source: call the provider's REST API directly
7. For `docs` / `mixed` source: call `parse_models_from_docs(seed_urls=…, guidance=…)` for the parts that aren't in an API
8. **Never hardcode models, voices, or language lists directly in the syncer.** Even when a provider has no API and the data appears static, always use `parse_models_from_docs` so that: (a) the data lands in `.sync-cache/` for review, (b) re-syncing picks up upstream changes automatically, and (c) the diff/apply workflow works correctly. Hardcoded data bypasses the cache, breaks the diff, and silently goes stale.
9. Populate `api_urls` and `docs_urls` in the returned `SyncResult` — these are stored in the DB and exposed via the public API
10. Add an env var to `naaviq/config.py` and `.env.example` if the syncer needs an API key
11. Register the provider **once** — add a `SyncerEntry(...)` line to `naaviq/sync/registry.py` with provider_id, display_name, type, and dotted syncer path. Both `scripts/sync.py` and `naaviq-admin` read from the same registry, so there's only one place to update.
12. Submit a PR

### Smoke testing a syncer

Each `naaviq/sync/*.py` has a `_main()` runner. Set the relevant env vars and run as a module:

```bash
uv sync --extra sync
ELEVENLABS_API_KEY=... ANTHROPIC_API_KEY=... uv run python -m naaviq.sync.elevenlabs
```

This prints the parsed models/voices but never touches the DB.

## Sync workflow

Three paths — all use the same sync scripts and produce identical DB state. They differ in **who performs the AI extraction** for `docs`/`mixed` providers:

### Path A: Claude Code (primary, zero API cost)

Ask Claude Code to sync a provider. For `docs`/`mixed` providers Claude Code itself does the agentic fetch-and-parse step, writing structured output to `.sync-cache/` before running the script. The script reads the cache and skips the internal AI parser entirely. **No `ANTHROPIC_API_KEY` is needed** — the AI work is attributed to your Claude Code session, not billed against a key.

```bash
# In your Claude Code conversation:
"sync cartesia to dev DB"
# → Claude fetches Cartesia docs, produces SyncModel/SyncVoice JSON, writes .sync-cache/
# → Claude then runs: uv run python scripts/sync.py cartesia --apply
```

Also:

```bash
uv run python scripts/sync.py                    # dry-run: show diff
uv run python scripts/sync.py cartesia --apply   # apply one provider
uv run python scripts/sync.py --apply            # apply all providers
uv run python scripts/promote.py --apply         # promote dev → prod (no AI, pure copy)
```

- `DATABASE_URL` in `.env` = dev DB
- `PROD_DATABASE_URL` in `.env` = prod DB (only needed for promote)

### Path B: Direct script run with `ANTHROPIC_API_KEY` (contributors / CI)

For contributors without Claude Code, or in CI, the script's internal AI parser calls the Anthropic API on cache miss. Set `ANTHROPIC_API_KEY` in `.env` and the parser runs automatically. `api`-source providers never need the key regardless of path.

```bash
export ANTHROPIC_API_KEY=sk-...
uv run python scripts/sync.py cartesia --apply    # AI parser runs inside the script
uv run python scripts/sync.py deepgram --apply    # no key needed (api source)
```

`docs`/`mixed` providers that use the AI parser: cartesia, elevenlabs, openai, google-cloud, sarvam, humeai, inworld, speechmatics, assemblyai, revai, and the other docs-based syncers.
`api` providers that never need the key: deepgram, azure, amazon-polly, murf, lmnt, rime, etc.

### Path C: Admin UI

Fetch → diff → apply via the `naaviq-admin` API and `naaviq-admin-ui` frontend. Good for visual diff review. Requires `ANTHROPIC_API_KEY` on the admin server for AI-parsed providers (the server has no Claude Code attached).

## Quick start

```bash
docker compose up -d           # start Postgres
cp .env.example .env
uv sync
uv run alembic upgrade head    # create tables
uv run uvicorn naaviq.main:app --reload
```

## Public API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/providers` | List all active providers |
| GET | `/v1/providers/{id}` | Get a single provider |
| GET | `/v1/providers/{id}/models` | List models (filters: `?type=stt\|tts`, `?capabilities=a,b`) |
| GET | `/v1/providers/{id}/voices` | List voices (filters: `?gender=`, `?capabilities=`, etc.) |
| GET | `/health` | Health check |

All list endpoints support **`?updated_since=<ISO-8601>`** for incremental polling — returns rows with `updated_at >= updated_since`. When this param is set, `include_deprecated` auto-flips to `true` so consumers see deprecation events (pass `include_deprecated=false` explicitly to override). Consumers poll by storing `max(row.updated_at)` from each batch and passing it back; `>=` can duplicate the boundary row, dedupe by `id` client-side.

## Related repos

- `naaviq-admin` — private admin API (sync trigger, diff apply, DB writes)
- `naaviq-admin-ui` — private admin frontend (sync button, diff review)
- `vaaniq` — main Vaaniq backend (consumes this public API)
