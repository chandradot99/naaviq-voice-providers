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

1. **Community** contributes sync scripts for new providers via PRs to this repo (`naaviq/sync/`)
2. **Your team** reviews + merges the PR
3. **Your team** clicks "Fetch" in `naaviq-admin-ui` → calls `naaviq-admin` API
4. **`naaviq-admin`** imports and runs the sync script → fetches live data → returns the result for review (no DB write)
5. **Your team** reviews the diff in admin UI (new models/voices added, deprecated ones flagged)
6. **Your team** clicks "Apply" → `naaviq-admin` upserts into the shared DB
7. **`naaviq-voice-providers`** (this repo) serves the updated data via the public read API

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
│   │   └── providers.py  — GET /v1/providers, /models, /voices
│   └── sync/
│       ├── base.py       — SyncResult contract + ProviderSyncer base class
│       ├── language.py   — BCP-47 normalization
│       ├── ai_parser.py  — Agentic Claude loop for parsing models from docs
│       ├── deepgram.py   — Deepgram syncer (api)
│       ├── cartesia.py   — Cartesia syncer (mixed: API voices + AI-parsed docs models)
│       └── elevenlabs.py — ElevenLabs syncer (mixed: API TTS + AI-parsed STT)
├── alembic/              — DB migrations
├── tests/
├── docker-compose.yml    — Postgres only (for local dev)
└── CLAUDE.md
```

## Database tables

- **`providers`** — Cartesia, ElevenLabs, OpenAI, Deepgram, Sarvam, etc.
- **`models`** — STT/TTS models per provider (languages, streaming, is_default)
- **`voices`** — TTS voices per provider (gender, category, languages, preview_url)

All tables use `deprecated_at` instead of hard deletes. Sync scripts never write to DB.

## Sync scripts

### Three source types

| Type | When to use | Example providers |
|---|---|---|
| `api` | Provider exposes a REST API for both models and voices | Deepgram |
| `docs` | No API — parse documentation with AI | Sarvam (planned) |
| `mixed` | API for some data (e.g., voices) + docs parsing for the rest (e.g., models) | Cartesia, ElevenLabs |

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

`"*"` is the wildcard for "supports many languages, no enumerated list" (e.g., ElevenLabs Scribe's ~99 languages). Always call `normalize_languages(langs)` before returning from any fetch method.

### The `meta` field

Both `SyncModel` and `SyncVoice` have a `meta: dict` for provider-specific data that doesn't belong in the core schema. Stored as JSONB. Used for capability flags, snapshot/EOL dates, latency hints, etc.

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
8. Add an env var to `naaviq/config.py` and `.env.example` if the syncer needs an API key
9. Register in `naaviq-admin/naaviq_admin/routers/providers.py` `_SYNCERS` dict
10. Submit a PR

### Smoke testing a syncer

Each `naaviq/sync/*.py` has a `_main()` runner. Set the relevant env vars and run as a module:

```bash
uv sync --extra sync
ELEVENLABS_API_KEY=... ANTHROPIC_API_KEY=... uv run python -m naaviq.sync.elevenlabs
```

This prints the parsed models/voices but never touches the DB.

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
| GET | `/v1/providers/{id}/models` | List models (filter: `?type=stt\|tts`) |
| GET | `/v1/providers/{id}/voices` | List voices (filter: `?gender=male\|female`) |
| GET | `/health` | Health check |

## Related repos

- `naaviq-admin` — private admin API (sync trigger, diff apply, DB writes)
- `naaviq-admin-ui` — private admin frontend (sync button, diff review)
- `vaaniq` — main Vaaniq backend (consumes this public API)
