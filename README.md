# Naaviq

Open-source voice provider registry — STT/TTS models and voices.

A public read-only REST API at `providers.naaviq.ai` that serves up-to-date metadata about voice AI providers (Cartesia, ElevenLabs, Deepgram, OpenAI, Sarvam, …) so applications can discover models, voices, supported languages, and capabilities without scraping each provider's docs.

## What's in here

- **`naaviq/`** — FastAPI app (public read API) + SQLAlchemy ORM
- **`naaviq/sync/`** — provider sync scripts (one per provider). Community-contributable.
- **`alembic/`** — DB migrations
- **`tests/`** — basic API smoke tests

## Sync architecture

Each provider has a syncer that returns a `SyncResult(stt_models, tts_models, tts_voices, source, …)`. Three source types:

| Source | When | Examples |
|---|---|---|
| `api` | Provider exposes a REST models endpoint | Deepgram |
| `docs` | No API — parse docs with an AI model | Sarvam (planned) |
| `mixed` | Some endpoints exist, some require parsing docs | Cartesia, ElevenLabs |

For `docs` and `mixed` sources, `naaviq/sync/ai_parser.py` runs an agentic Claude loop with two tools (`fetch_url`, `return_models`) and a JSONSchema-constrained terminal call to extract structured `SyncModel` objects.

## Quick start

```bash
docker compose up -d           # start Postgres
cp .env.example .env
uv sync
uv run alembic upgrade head    # create tables
uv run uvicorn naaviq.main:app --reload
```

## Running a sync script locally

```bash
uv sync --extra sync           # installs anthropic SDK for AI-parser providers
ELEVENLABS_API_KEY=... ANTHROPIC_API_KEY=... uv run python -m naaviq.sync.elevenlabs
```

Sync scripts only print the result — they never write to the DB. The write side lives in the private `naaviq-admin` service.

## Tests

```bash
uv sync --extra dev
uv run pytest
```

## Public API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/providers` | List all active providers |
| GET | `/v1/providers/{id}` | Get a single provider |
| GET | `/v1/providers/{id}/models` | List models (filter: `?type=stt\|tts`) |
| GET | `/v1/providers/{id}/voices` | List voices (filter: `?gender=male\|female`) |
| GET | `/health` | Health check |

## Contributing a new provider

1. Add `naaviq/sync/{provider_id}.py` — subclass `ProviderSyncer`, implement `sync()`
2. Use `normalize_languages()` on every language list (BCP-47 with uppercase region)
3. For doc-based providers, call `parse_models_from_docs(seed_urls=…, guidance=…)`
4. Open a PR — your team merges, then triggers sync from the admin UI

License: Apache 2.0
