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
3. **Your team** clicks "Sync" in `naaviq-admin-ui` → calls `naaviq-admin` API
4. **`naaviq-admin`** imports and runs the sync script → fetches live data → computes a diff against current DB state
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

## Project structure

```
naaviq-voice-providers/
├── naaviq/
│   ├── main.py           — FastAPI app entry point
│   ├── config.py         — settings (DATABASE_URL from .env)
│   ├── db.py             — async SQLAlchemy engine + get_db
│   ├── models.py         — ORM: Provider, Model, Voice
│   ├── schemas.py        — Pydantic response schemas
│   ├── limiter.py        — slowapi rate limiter
│   ├── routers/
│   │   └── providers.py  — GET /v1/providers, /models, /voices
│   └── sync/
│       ├── base.py       — SyncResult contract + ProviderSyncer base class
│       ├── cartesia.py   — Cartesia syncer (API-based)
│       ├── elevenlabs.py — ElevenLabs syncer (API-based)
│       ├── openai.py     — OpenAI syncer (API-based)
│       ├── deepgram.py   — Deepgram syncer (API-based)
│       └── sarvam.py     — Sarvam syncer (doc-based, AI-parsed)
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

### Two source types

| Type | When to use | Example providers |
|---|---|---|
| `api` | Provider exposes a REST API | Cartesia, ElevenLabs, OpenAI, Deepgram |
| `docs` | No API — parse documentation with AI (Claude/GPT/Gemini) | Sarvam |

### Language normalization (`naaviq/sync/language.py`)

Every provider uses a different language format. All languages are normalized to **BCP-47 with uppercase region** before storing:

| Provider | Their format | Normalized |
|---|---|---|
| Deepgram | `"en-us"`, `"fr-fr"` | `"en-US"`, `"fr-FR"` |
| Cartesia | `"en"`, `"fr"` | `"en"`, `"fr"` |
| ElevenLabs | `"en"`, `"hu"` | `"en"`, `"hu"` |
| OpenAI | multilingual (`"*"`) | `"*"` |
| Sarvam | `"hi-IN"`, `"en-IN"` | already correct |

Always call `normalize_languages(langs)` before returning from any fetch method.

### The `meta` field

Both `SyncModel` and `SyncVoice` have a `meta: dict` for provider-specific data that doesn't belong in the core schema. This is stored as JSONB in Postgres.

**Model meta examples:**
```python
# Deepgram
meta = {"tier": "nova", "batch": True, "formatted_output": True}
# ElevenLabs
meta = {"max_characters": 10000, "latency_optimization": True}
```

**Voice meta examples:**
```python
# Deepgram TTS
meta = {"accent": "american", "age": "adult", "use_cases": ["customer-service"], "tags": ["clear"]}
# ElevenLabs
meta = {"accent": "british", "age": "young", "labels": {"use_case": "narration"}, "stability": 0.5}
# Cartesia
meta = {"emotion_support": True, "volume_support": True}
# OpenAI
meta = {"instructions_support": True, "model_exclusive": "gpt-4o-mini-tts"}
```

### The sync contract (`naaviq/sync/base.py`)

Providers are added manually from admin UI. STT models, TTS models, and TTS voices are synced **independently** — three separate buttons, three separate diffs.

Each sync script implements one method — `sync()`. Internal helpers are private.

```python
class MyProviderSyncer(ProviderSyncer):
    provider_id = "myprovider"
    source = "api"  # or "docs"

    async def sync(self) -> SyncResult:
        data = await self._fetch_raw()          # private helper
        models = self._parse_models(data)       # private helper
        voices = self._parse_voices(data)       # private helper
        return SyncResult(models=models, voices=voices, source=self.source)
```

`SyncResult` contains both models and voices. naaviq-admin diffs them separately against the DB before applying.

### Adding a new provider

1. Create `naaviq/sync/{provider_id}.py`
2. Subclass `ProviderSyncer`, set `provider_id` and `source`
3. Implement all three fetch methods (return empty for unsupported types)
4. Use `normalize_languages()` on all language lists
5. Put provider-specific extras in `meta` dict
6. For `api` source: call the provider's REST API directly
7. For `docs` source: fetch docs page → pass to AI model → parse into `SyncModel`/`SyncVoice` list
8. Submit a PR — your team reviews, merges, then triggers each sync independently from admin UI

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
