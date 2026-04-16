# Naaviq — Provider Sync Implementation Plan

## Architecture recap

| Repo | Role |
|---|---|
| `naaviq-voice-providers` | Public. ORM models, Alembic migrations, public read API, sync scripts |
| `naaviq-admin` | Private. Admin write API — triggers sync, diffs, applies to DB |
| `naaviq-admin-ui` | Private. Admin frontend — fetch → diff → apply workflow |

---

## Source types for sync scripts

| Type | When to use | How it works |
|---|---|---|
| `api` | Provider exposes a REST API for listing models/voices | Call the API directly with httpx |
| `docs` | No API — all data is in documentation only | AI-powered doc parser (see below) |
| `mixed` | Some data from API, some from docs | Combine both approaches within the same syncer |

The `source` value is informational — it labels the dominant approach. Provenance details (URLs fetched, model used, token count) belong in `SyncResult.notes`.

---

## AI-powered doc parser (`naaviq/sync/ai_parser.py`)

For data that only exists in documentation, we use an **agentic Claude loop** with tool use.

```
1. Give Claude: a seed URL + system prompt + two tools (fetch_url, return_models)
2. Claude fetches the seed page, reads it, decides which links to follow
3. Claude keeps fetching until it has all the data
4. Claude calls return_models → we extract structured SyncModel[]
```

### Why this approach
- Claude drives link-following — no brittle HTML parsing on our side
- Works across multi-page docs automatically
- AI doesn't miss fields (unlike hardcoding)
- When provider adds a new model, next sync picks it up automatically

### Function signature

```python
async def parse_models_from_docs(
    seed_url: str,
    provider_id: str,
    model_type: Literal["stt", "tts"],
    guidance: str = "",          # provider-specific hints (e.g., "mark latest sonic as is_default")
    api_key: str | None = None,  # ANTHROPIC_API_KEY; falls back to env
) -> tuple[list[SyncModel], dict]:
    ...
```

Returns `(models, notes)` where `notes` is a dict with `urls_fetched: list[str]`, `model: str`, `input_tokens: int`, `output_tokens: int` — caller merges into `SyncResult.notes`.

### Tools given to Claude

- **`fetch_url(url: str)`** — returns plain-text page contents (HTML→text, max 60k chars). Errors come back as a tool_result error so Claude can retry or follow a different link.
- **`return_models(models: list[SyncModelSchema])`** — terminal tool. Schema mirrors `SyncModel` exactly (model_id, display_name, type, languages BCP-47, streaming, is_default, description, meta). Loop exits the moment Claude calls this.

### System prompt structure

The system prompt is **prompt-cached** (per `claude-api` skill guidance — same prompt each call):
1. The exact `SyncModel` JSON schema (keep in sync with `base.py`)
2. Naming/normalization rules (BCP-47 uppercase region; `meta` for extras)
3. Hard rule: at most one `is_default=True` per `(provider, type)` pair
4. Behavioral rule: explore links from the seed page until confident; then call `return_models` exactly once

The user message contains: provider_id, model_type, seed_url, and `guidance`.

### Model

`claude-sonnet-4-6` — strong tool-use adherence and reasoning, ample headroom for messy docs. Cost is negligible at this volume (~$7/year across all providers), so we default to Sonnet for safety rather than tuning per-provider. Override per-call if a specific provider needs a different model.

### Safety & failure modes

- **Max 10 iterations** per parse call. If Claude hasn't called `return_models` by then → raise `AIParserError` (don't return partial data — the admin diff would deprecate everything missing).
- **Max 60,000 chars per fetched page**. If a page is larger, truncate and append `[truncated]` so Claude knows to follow links instead.
- **Max 15 distinct URLs fetched** per call (prevents runaway link-following).
- **Invalid `return_models` payload** (Pydantic validation fails) → raise; don't try to repair.
- **httpx.HTTPError on `fetch_url`** → return as a tool error to Claude (it can pick another link).

---

## Changes required per component

### `naaviq-voice-providers`

#### `naaviq/sync/base.py`
- Update `source` literal in **both places** to `Literal["api", "docs", "mixed"]`:
  - `SyncResult.source` (line 86)
  - `ProviderSyncer.source` ClassVar (line 118)
- The docstring example at line 110-114 is outdated (uses `models=`/`voices=`); update to use `stt_models`, `tts_models`, `tts_voices`.

#### `naaviq/sync/ai_parser.py` ← new file
- Implements `parse_models_from_docs(...)` per the spec above.
- Uses `anthropic.AsyncAnthropic` with tool use + prompt caching on the system block.
- Standalone-runnable via `if __name__ == "__main__"` (mirrors `deepgram.py:174`) for quick smoke tests against a known seed URL.

#### `naaviq/sync/cartesia.py` ← new file
- `CartesiaSyncer` — `provider_id = "cartesia"`, `source = "mixed"`
- Returns: `stt_models=[]`, `tts_models=[<from docs>]`, `tts_voices=[<from API>]` (Cartesia is TTS-only)
- **Voices**: paginated `GET https://api.cartesia.ai/voices`, cursor via `starting_after`, with `expand[]=preview_file_url`. Stop when `has_more=False`. **Cap at 50 pages** (~5000 voices) as a safety guard.
- **Models**: `parse_models_from_docs(seed_url=..., provider_id="cartesia", model_type="tts", guidance="The latest sonic version is the default. Sonic-1 is legacy.")`
- Auth: `Authorization: Bearer <CARTESIA_API_KEY>`. Confirm the current `Cartesia-Version` header value before committing — the value in earlier drafts (`2025-04-16`) is over a year old as of today (2026-04-16) and may be stale.
- Gender map: `masculine→male`, `feminine→female`, `gender_neutral→neutral`
- Filter: only `is_public=True` voices
- Derive `accent` from language region where present (e.g., `en_GB` → `"british"`, `en_AU` → `"australian"`); leave `None` for bare `"en"`.
- All `category="premade"` (public Cartesia voices are premade only).
- **Preview URL caveat**: Cartesia's `preview_file_url` may be a presigned/expiring URL. Document this; consumers should treat preview URLs as "fetch-soon-after-sync" or accept periodic 403s.
- `SyncResult.notes` merges parser provenance + voice fetch summary (e.g., `"3 doc pages, 4 tts models / 2 voice pages, 187 voices"`).
- Standalone `_main()` runner for smoke testing.

#### `naaviq/sync/__init__.py`
- Re-export `parse_models_from_docs` alongside the existing exports.

#### `pyproject.toml`
- Add `anthropic>=0.40` under `[project.optional-dependencies]` as a `sync` extra (required for the standalone runner; admin pulls anthropic directly — see below).

#### `.env.example`
- Add `CARTESIA_API_KEY=`
- Add `ANTHROPIC_API_KEY=` (only needed when running sync scripts standalone)

#### `naaviq/config.py`
- Add `cartesia_api_key: str = ""`
- Add `anthropic_api_key: str = ""`

---

### `naaviq-admin`

#### `pyproject.toml`
- Add `anthropic>=0.40` as a **direct** dependency. Reason: admin imports `CartesiaSyncer` at runtime via `importlib`; if anthropic isn't installed, the import fails. Direct dep avoids relying on the optional `sync` extra of the path-installed package.

#### `naaviq_admin/config.py`
- Add `cartesia_api_key: str = ""`
- Add `anthropic_api_key: str = ""` (forwarded to sync scripts via env)

#### `.env.example`
- Add `CARTESIA_API_KEY=`
- Add `ANTHROPIC_API_KEY=`

#### `naaviq_admin/routers/providers.py`
- Add `"cartesia": "naaviq.sync.cartesia.CartesiaSyncer"` to the `_SYNCERS` dict (line 26-28; `deepgram` is already registered).

---

### `naaviq-admin-ui`

No changes required — the fetch → diff → apply workflow already handles any provider generically.

---

## Cartesia API reference

| Field | Source | Notes |
|---|---|---|
| `voice_id` | `id` | UUID |
| `display_name` | `name` | |
| `gender` | `gender` | masculine/feminine/gender_neutral → male/female/neutral |
| `category` | hardcoded | `"premade"` for all public voices |
| `languages` | `language` | Single string e.g. `"en"`, `"en_GB"` (underscore → hyphen, `normalize_language` handles it) |
| `accent` | derived from language region | `en_GB` → `"british"`, `en_AU` → `"australian"`, etc. `None` for bare codes |
| `description` | `description` | May be null |
| `preview_url` | `preview_file_url` | Only present when `expand[]=preview_file_url` sent. May be presigned/expiring. |
| `meta` | — | Empty `{}` — Cartesia doesn't expose extra metadata |

### Voice pagination
```
GET /voices?limit=100&expand[]=preview_file_url
→ { "data": [...], "has_more": bool }
Loop with starting_after=<last_id> until has_more=False (cap: 50 pages)
```

### Models (no API — AI-parsed from docs)
```
Seed URL: https://docs.cartesia.ai/build-with-cartesia/tts-models/latest
Expected models: sonic-3 (default), sonic-2, sonic-turbo, sonic (legacy)
Guidance to AI parser: "The latest sonic version is the default."
```

---

## Implementation order

1. `base.py` — update `source` literals (both places) + fix outdated docstring
2. `ai_parser.py` — build the agentic parser; smoke-test standalone against the Cartesia models seed URL **before** wiring into Cartesia
3. `cartesia.py` — voice API + invoke parser; smoke-test standalone
4. `naaviq-voice-providers`: `pyproject.toml` (sync extra), `config.py`, `.env.example`, `__init__.py` re-export
5. `naaviq-admin`: `pyproject.toml` (anthropic dep), `config.py`, `.env.example`
6. `naaviq-admin/routers/providers.py` — register `cartesia` in `_SYNCERS`
7. End-to-end test: add cartesia provider in admin UI → Fetch → review diff → Apply

---

## Future providers (same pattern)

| Provider | Voices | Models | Source |
|---|---|---|---|
| ElevenLabs | REST API | REST API | `api` |
| OpenAI | hardcoded (few voices) | REST API | `api` / `mixed` |
| Sarvam | AI-parsed docs | AI-parsed docs | `docs` |
| PlayHT | REST API | hardcoded or docs | `api` / `mixed` |
