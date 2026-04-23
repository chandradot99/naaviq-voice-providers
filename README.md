# Naaviq

Open-source voice provider registry — STT/TTS models and voices.

A public read-only REST API that serves up-to-date metadata about voice AI providers so applications can discover models, voices, supported languages, and capabilities without scraping each provider's docs.

**32 providers covered:** Deepgram, Cartesia, ElevenLabs, OpenAI, Google Cloud, Sarvam, Azure Speech, Amazon Polly, Hume AI, Inworld AI, Murf AI, Speechmatics, LMNT, Rime AI, AssemblyAI, Rev AI, Gladia, MiniMax, IBM Watson, Neuphonic, Amazon Transcribe, Resemble AI, Fish Audio, Unreal Speech, Smallest AI, Lovo AI, Mistral AI, WellSaid Labs, CAMB.ai, Speechify, Typecast AI, Groq

## Live API

```
Base URL: https://naaviq-voice-providers-production.up.railway.app
```

Try it now — no auth required:

```bash
# List all providers
curl https://naaviq-voice-providers-production.up.railway.app/v1/providers

# Get a single provider
curl https://naaviq-voice-providers-production.up.railway.app/v1/providers/elevenlabs

# List TTS models for a provider
curl https://naaviq-voice-providers-production.up.railway.app/v1/providers/elevenlabs/models?type=tts

# List voices
curl https://naaviq-voice-providers-production.up.railway.app/v1/providers/elevenlabs/voices
```

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/v1/providers` | List all active providers |
| GET | `/v1/providers/{id}` | Get a single provider (includes `api_urls`, `docs_urls`) |
| GET | `/v1/providers/{id}/models` | List models (filter: `?type=stt\|tts`, `?capabilities=a,b`) |
| GET | `/v1/providers/{id}/voices` | List voices (filter: `?gender=male\|female`, `?capabilities=a,b`) |
| GET | `/health` | Health check |

All list endpoints support `?updated_since=<ISO-8601>` for incremental polling.

## What's in here

- **`naaviq/`** — FastAPI app (public read API) + SQLAlchemy ORM
- **`naaviq/sync/`** — provider sync scripts (one per provider). Community-contributable.
- **`scripts/`** — `sync.py` (dev DB) and `promote.py` (dev → prod)
- **`alembic/`** — DB migrations
- **`tests/`** — basic API smoke tests

## Running locally

**Requirements:** Python 3.12+, [uv](https://docs.astral.sh/uv/), Docker

```bash
# 1. Clone and install dependencies
git clone https://github.com/chandradot99/naaviq-voice-providers
cd naaviq-voice-providers
uv sync

# 2. Start Postgres
docker compose up -d

# 3. Configure environment
cp .env.example .env
# Edit .env — set DATABASE_URL to the local Postgres URL from docker-compose

# 4. Run migrations
uv run alembic upgrade head

# 5. Start the API server
uv run uvicorn naaviq.main:app --reload
```

The API is now available at `http://localhost:8000`.

## Sync architecture

Each provider has a syncer that returns a `SyncResult(stt_models, tts_models, tts_voices, source, api_urls, docs_urls, …)`. Three source types:

| Source | When | Examples |
|---|---|---|
| `api` | Provider exposes a REST models/voices endpoint | Deepgram, Azure, Amazon Polly, IBM |
| `docs` | No API — parse docs with an AI model | OpenAI, Sarvam, Speechmatics, AssemblyAI, Rev AI, Gladia, Amazon Transcribe, Unreal Speech |
| `mixed` | Some endpoints exist, some require parsing docs | Cartesia, ElevenLabs, Google Cloud, Hume AI, Inworld, Murf, Rime AI, LMNT, Fish Audio, Groq, CAMB.ai |

For `docs` and `mixed` sources, `naaviq/sync/ai_parser.py` runs an agentic Claude loop to extract structured `SyncModel` objects from documentation pages. Each provider row stores `api_urls` and `docs_urls` so consumers can trace where the data came from.

## Syncing providers to the DB

There are two ways to sync. Both use the same `naaviq/sync/*.py` scripts and produce identical DB state — they differ only in **who performs the AI extraction** for `docs`/`mixed` providers.

### Path A — Claude Code (recommended for maintainers)

Ask Claude Code (in your terminal) to sync a provider. For `docs`/`mixed` providers, Claude Code fetches the docs, produces structured JSON, writes it to `.sync-cache/`, and then runs the sync script. The script reads the cache, skips the internal AI parser, and never calls the Anthropic API. **No `ANTHROPIC_API_KEY` needed.**

```
# In your Claude Code conversation:
"sync cartesia to dev DB"
```

Claude Code then runs:

```bash
uv run python scripts/sync.py cartesia --apply
```

### Path B — Script + `ANTHROPIC_API_KEY` (contributors / CI)

If you don't have Claude Code, the script's built-in AI parser will call the Anthropic API on cache miss. Set `ANTHROPIC_API_KEY` in `.env` and run the script directly:

```bash
export ANTHROPIC_API_KEY=sk-...
uv run python scripts/sync.py                    # dry-run all providers, shows diff
uv run python scripts/sync.py cartesia deepgram  # dry-run two providers
uv run python scripts/sync.py cartesia --apply   # fetch + apply to dev DB
uv run python scripts/sync.py --apply            # apply all providers
```

`api`-source providers (deepgram, azure, amazon-polly, ibm) never need the key.
All other providers are `docs` or `mixed` and do.

### Promote dev → prod

No AI involved — pure data copy.

```bash
uv run python scripts/promote.py           # dry-run
uv run python scripts/promote.py --apply   # write to prod DB
```

Requires `DEV_DATABASE_URL` (source) and `DATABASE_URL` (prod target) in `.env`. See `CLAUDE.md` for the full sync workflow reference.

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

CI runs lint (`ruff`), migrations (`alembic upgrade head`), and `pytest` against a Postgres service container on every push and PR. Deploys to Railway are gated on CI passing.

## Contributing a new provider

1. Add `naaviq/sync/{provider_id}.py` — subclass `ProviderSyncer`, implement `sync()`
2. Use `normalize_languages()` on every language list (BCP-47 with uppercase region)
3. For doc-based providers, call `parse_models_from_docs(seed_urls=…, guidance=…)`
4. Populate `api_urls` and `docs_urls` in the returned `SyncResult`
5. Register with a `SyncerEntry(...)` line in `naaviq/sync/registry.py` (single source of truth — both the sync script and the admin API read from it)
6. Open a PR — your team merges, then triggers sync from the admin UI

See `CLAUDE.md` for the full adding-a-provider checklist.

License: Apache 2.0
