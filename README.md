# Naaviq

Open-source voice provider registry — STT/TTS models and voices.

A public read-only REST API at `providers.naaviq.ai` that serves up-to-date metadata about voice AI providers (Deepgram, Cartesia, ElevenLabs, OpenAI, Google Cloud, Sarvam, Azure, Amazon Polly, Hume AI, Inworld AI, Murf AI, Speechmatics, LMNT, Rime AI, AssemblyAI, Rev AI, Gladia, MiniMax, IBM Watson, Neuphonic) so applications can discover models, voices, supported languages, and capabilities without scraping each provider's docs.

## What's in here

- **`naaviq/`** — FastAPI app (public read API) + SQLAlchemy ORM
- **`naaviq/sync/`** — provider sync scripts (one per provider). Community-contributable.
- **`scripts/`** — `sync.py` (dev DB) and `promote.py` (dev → prod)
- **`alembic/`** — DB migrations
- **`tests/`** — basic API smoke tests

## Sync architecture

Each provider has a syncer that returns a `SyncResult(stt_models, tts_models, tts_voices, source, api_urls, docs_urls, …)`. Three source types:

| Source | When | Examples |
|---|---|---|
| `api` | Provider exposes a REST models/voices endpoint | Deepgram, Azure, Amazon Polly, Rime AI |
| `docs` | No API — parse docs with an AI model | OpenAI, Sarvam, Speechmatics, AssemblyAI, Rev AI |
| `mixed` | Some endpoints exist, some require parsing docs | Cartesia, ElevenLabs, Hume AI, Inworld AI, LMNT |

For `docs` and `mixed` sources, `naaviq/sync/ai_parser.py` runs an agentic Claude loop to extract structured `SyncModel` objects from documentation pages. Each provider row stores `api_urls` and `docs_urls` so consumers can trace where the data came from.

## Quick start

```bash
docker compose up -d           # start Postgres
cp .env.example .env
uv sync
uv run alembic upgrade head    # create tables
uv run uvicorn naaviq.main:app --reload
```

## Syncing providers to the DB

```bash
# Dry-run (default) — shows diff, no writes
uv run python scripts/sync.py
uv run python scripts/sync.py cartesia deepgram

# Apply to dev DB
uv run python scripts/sync.py --apply
uv run python scripts/sync.py cartesia --apply

# Promote dev → prod
uv run python scripts/promote.py          # dry-run
uv run python scripts/promote.py --apply  # write to prod
```

No `ANTHROPIC_API_KEY` needed when running via Claude Code — it extracts docs-based provider data and writes local cache files that the sync scripts read automatically. See `CLAUDE.md` for details.

## Running a single syncer locally (smoke test)

```bash
uv sync --extra sync           # installs anthropic SDK for AI-parser providers
ELEVENLABS_API_KEY=... ANTHROPIC_API_KEY=... uv run python -m naaviq.sync.elevenlabs
```

Smoke tests only print the result — they never write to the DB.

## Tests

```bash
uv sync --extra dev
uv run pytest
```

## Public API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/providers` | List all active providers |
| GET | `/v1/providers/{id}` | Get a single provider (includes `api_urls`, `docs_urls`) |
| GET | `/v1/providers/{id}/models` | List models (filter: `?type=stt\|tts`) |
| GET | `/v1/providers/{id}/voices` | List voices (filter: `?gender=male\|female`) |
| GET | `/health` | Health check |

## Contributing a new provider

1. Add `naaviq/sync/{provider_id}.py` — subclass `ProviderSyncer`, implement `sync()`
2. Use `normalize_languages()` on every language list (BCP-47 with uppercase region)
3. For doc-based providers, call `parse_models_from_docs(seed_urls=…, guidance=…)`
4. Populate `api_urls` and `docs_urls` in the returned `SyncResult`
5. Register in `scripts/sync.py` (`_SYNCERS` + `_PROVIDER_META`)
6. Open a PR — your team merges, then triggers sync from the admin UI

See `CLAUDE.md` for the full adding-a-provider checklist.

License: Apache 2.0
