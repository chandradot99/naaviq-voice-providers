"""
AI-powered documentation parser using Claude with tool use.

Used by sync scripts when a provider doesn't expose a REST API for models or
voices — Claude fetches the docs page, follows links as needed, and returns
structured SyncModel or SyncVoice objects via a terminal tool call.

Public functions:
  parse_models_from_docs(seed_urls, provider_id, model_type, guidance, api_key)
  parse_voices_from_docs(seed_urls, provider_id, guidance, api_key)

Safety guards (shared):
  - Max 15 iterations per parse call
  - Max 60,000 chars per fetched page (truncated with marker)
  - Max 15 distinct URLs fetched per call
  - Invalid terminal payload raises AIParserError (no partial data —
    the admin diff would deprecate everything we miss)
"""

from __future__ import annotations

import os
import sys
from html.parser import HTMLParser
from typing import Literal

import httpx
from anthropic import (
    APIConnectionError,
    APIStatusError,
    AsyncAnthropic,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

from naaviq.sync.base import HTTP_TIMEOUT, SyncModel, SyncVoice
from naaviq.sync.language import normalize_languages

MODEL = os.getenv("NAAVIQ_AI_PARSER_MODEL", "claude-haiku-4-5-20251001")
MAX_ITERATIONS = 15
MAX_PAGE_CHARS = 60_000
MAX_URLS = 15
MAX_TOKENS_PER_TURN = 8000

# When this many iterations remain, append a nudge to every tool_result asking
# Claude to wrap up and call the terminal tool. Stops the model from infinite-fetching.
NUDGE_THRESHOLD = 3


class AIParserError(Exception):
    pass


# ── System prompts ─────────────────────────────────────────────────────────────

_MODELS_SYSTEM_PROMPT = """You are a documentation parser for an open-source voice provider registry.

Given a seed documentation URL for a voice provider, fetch the page (and follow links as needed), then return a structured list of models.

Tools:
  - fetch_url(url): returns plain-text page contents
  - return_models(models): TERMINAL — call exactly once when you have all models

SyncModel schema (each item in the `models` array):
  - model_id (str, required)         — provider's stable identifier (e.g., "sonic-3", "nova-3")
  - display_name (str, required)     — human-readable (e.g., "Sonic 3", "Nova 3")
  - type (str, required)             — "stt" or "tts" (match the type the user asks for)
  - languages (list[str])            — BCP-47 with uppercase region: "en", "en-US", "hi-IN". Use ["*"] only when docs explicitly say multilingual.
  - streaming (bool, default true)
  - is_default (bool, default false) — at most ONE per (provider, type). Pick the latest/recommended.
  - lifecycle (str, default "ga") — one of "alpha" | "beta" | "ga". Use alpha/beta when docs explicitly label the model as such (e.g., "Preview", "Experimental", "Beta"). Otherwise "ga". NEVER emit "deprecated" — that's handled downstream.
  - description (str | omit)
  - eol_date (str | omit)            — "YYYY-MM-DD" if the docs state an end-of-life date for this model
  - sample_rates_hz (list[int] | omit)  — supported sample rates in Hz. TTS=output rates, STT=input rates. E.g., [8000, 16000, 24000, 48000]. Omit if docs don't specify.
  - audio_formats (list[str] | omit)    — audio formats. TTS=output formats produced, STT=input formats accepted. Lowercase, e.g., ["mp3", "wav", "pcm", "opus", "flac", "ogg"]. Omit if docs don't specify.
  - max_text_chars (int | omit)         — TTS-only. Maximum characters per single request as documented. Omit for STT or if not specified.
  - max_audio_seconds (int | omit)      — STT-only. Maximum audio duration per single request, in seconds. Omit for TTS or if not specified.
  - capabilities (list[str] | omit)     — canonical capability flags. ONLY use strings from this controlled vocabulary (no freeform):
      STT: word_timestamps | speaker_diarization | punctuation | profanity_filter | custom_vocabulary |
           language_detection | translation | sentiment | pii_redaction | summarization | topic_detection
      TTS: emotion | voice_cloning | voice_design | ssml | phoneme_input | prosody_control | style_control | multi_speaker
      Include a flag only when the docs clearly state the model supports the feature. Skip unknown/custom features (put them in `meta` if needed).
  - regions (list[str] | omit)       — deployment regions. Canonical vocab ONLY: us | eu | asia | global.
      Use ["global"] if docs say the model is available worldwide / no regional restriction.
      Use a specific list like ["us","eu"] if docs restrict availability to those regions.
      Omit entirely when docs don't mention regions — don't guess "global" as a fallback.
  - pricing (object | omit)          — per-model pricing; omit if not on docs/pricing page. Shape:
      {
        "unit":       "character" | "minute" | "second" | "word" | "token" | "request" | "hour",
        "price_usd":  <float>,                                                  # cost per unit in USD
        "free_quota": {"amount": <int>, "unit": <str>, "period": "month"|"day"|"once"} | omit,
        "variants":   [{"applies_to": <str>, "unit": <str>, "price_usd": <float>}, ...] | omit,
        "as_of":      "YYYY-MM-DD",                                             # capture date
        "source_url": "<pricing-page-url>",
        "notes":      "<short qualifier>" | omit
      }
    Only populate when you find explicit numeric pricing in the docs — never guess.
  - meta (object)                    — provider-specific extras that don't fit elsewhere

Rules:
  1. Don't invent fields. If docs don't say, omit (or use the default).
  2. BCP-47 with uppercase region. "*" only when docs explicitly say multilingual.
  3. Mark exactly ONE model as is_default per type when docs indicate a default/latest/recommended.
  4. Follow links from the seed page only when the seed lacks the data.
  5. When you have all models, call return_models ONCE. Don't call it twice.
  6. If a page is truncated, follow links to subpages instead of guessing.
  7. For pricing: only include if an explicit number is on the pricing/docs page. Omit the whole object otherwise — never make up a price.
"""

_VOICES_SYSTEM_PROMPT = """You are a documentation parser for an open-source voice provider registry.

Given documentation URLs for a voice provider, fetch the page(s) (and follow links as needed), then return a structured list of TTS voices (speakers).

Tools:
  - fetch_url(url): returns plain-text page contents
  - return_voices(voices): TERMINAL — call exactly once when you have all voices

SyncVoice schema (each item in the `voices` array):
  - voice_id (str, required)          — provider's stable identifier used in the API (e.g., "meera", "alloy")
  - display_name (str, required)      — human-readable name (title-case of voice_id if docs don't give a different name)
  - gender ("male" | "female" | "neutral" | omit) — infer from docs; omit if not mentioned
  - category ("premade" | "cloned" | "generated") — default "premade" for built-in voices
  - languages (list[str])             — BCP-47 with uppercase region. Use ["*"] if the voice supports all provider languages (multilingual). Use [] only when languages are unknown/unmapped.
  - description (str | omit)
  - preview_url (str | omit)          — only if a direct audio sample URL is explicitly in the docs
  - accent (str | omit)               — e.g., "british", "american", "indian"
  - age (str | omit)                  — e.g., "young", "middle-aged", "old"
  - use_cases (list[str] | omit)      — e.g., ["narration", "IVR"]
  - tags (list[str] | omit)           — e.g., ["deep", "friendly"]
  - compatible_models (list[str])     — TTS model IDs this voice works with. ["*"] = works with all models. [] = unknown.
  - capabilities (list[str] | omit)   — voice-level capability flags. Canonical vocab (no freeform):
      emotion | multilingual_native
      Most capabilities live at the model level; only set a voice capability when a feature is opt-in per voice within a model that exposes it generally.
  - meta (object | omit)              — provider-specific extras

Rules:
  1. Don't invent fields. If docs don't say, omit.
  2. Languages in BCP-47 with uppercase region. ["*"] means the voice supports all provider languages; [] means unknown.
  3. Don't invent preview_url if not explicitly in the docs.
  4. category defaults to "premade" for all built-in provider voices.
  5. Populate compatible_models with the TTS model ID(s) this voice works with. Use ["*"] if it works with all models. Use [] only if unknown/unclear.
  6. Call return_voices ONCE with all voices found. Don't call it twice.
  7. Follow links if the seed page lacks complete voice data.
"""


# ── Tool definitions ───────────────────────────────────────────────────────────

_FETCH_URL_TOOL = {
    "name": "fetch_url",
    "description": "Fetch a URL and return its text content. Use this to read documentation pages and follow links.",
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute URL to fetch"},
        },
        "required": ["url"],
    },
}

_MODELS_TOOLS = [
    _FETCH_URL_TOOL,
    {
        "name": "return_models",
        "description": "Terminal tool. Call exactly once with the complete list of models you've found.",
        "input_schema": {
            "type": "object",
            "properties": {
                "models": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "model_id":     {"type": "string"},
                            "display_name": {"type": "string"},
                            "type":         {"type": "string", "enum": ["stt", "tts"]},
                            "languages":    {"type": "array", "items": {"type": "string"}},
                            "streaming":    {"type": "boolean"},
                            "is_default":   {"type": "boolean"},
                            "lifecycle":    {"type": "string", "enum": ["alpha", "beta", "ga"]},
                            "description":      {"type": "string"},
                            "eol_date":         {"type": "string"},
                            "sample_rates_hz":  {"type": "array", "items": {"type": "integer"}},
                            "audio_formats":    {"type": "array", "items": {"type": "string"}},
                            "max_text_chars":   {"type": "integer"},
                            "max_audio_seconds":{"type": "integer"},
                            "capabilities":     {"type": "array", "items": {"type": "string"}},
                            "regions":          {"type": "array", "items": {"type": "string", "enum": ["us", "eu", "asia", "global"]}},
                            "pricing":          {"type": "object"},
                            "meta":             {"type": "object"},
                        },
                        "required": ["model_id", "display_name", "type"],
                    },
                },
            },
            "required": ["models"],
        },
    },
]

_VOICES_TOOLS = [
    _FETCH_URL_TOOL,
    {
        "name": "return_voices",
        "description": "Terminal tool. Call exactly once with the complete list of voices you've found.",
        "input_schema": {
            "type": "object",
            "properties": {
                "voices": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "voice_id":     {"type": "string"},
                            "display_name": {"type": "string"},
                            "gender":       {"type": "string", "enum": ["male", "female", "neutral"]},
                            "category":     {"type": "string", "enum": ["premade", "cloned", "generated"]},
                            "languages":    {"type": "array", "items": {"type": "string"}},
                            "description":  {"type": "string"},
                            "preview_url":  {"type": "string"},
                            "accent":       {"type": "string"},
                            "age":          {"type": "string"},
                            "use_cases":    {"type": "array", "items": {"type": "string"}},
                            "tags":         {"type": "array", "items": {"type": "string"}},
                            "compatible_models": {"type": "array", "items": {"type": "string"}},
                            "capabilities": {"type": "array", "items": {"type": "string"}},
                            "meta":         {"type": "object"},
                        },
                        "required": ["voice_id", "display_name"],
                    },
                },
            },
            "required": ["voices"],
        },
    },
]


# ── Public API ─────────────────────────────────────────────────────────────────


def notes_to_str(notes: dict | None) -> str | None:
    """Render an ai_parser notes dict as a one-line provenance string for SyncResult.notes.

    `sync_runs.notes` is VARCHAR, so syncers that pass parser output straight
    through must flatten the dict first.
    """
    if not notes:
        return None
    if notes.get("source") == "cache":
        return f"cache: {notes.get('path', '')}"
    return (
        f"ai-parser: model={notes.get('model', '?')}, "
        f"in={notes.get('input_tokens', 0)}, out={notes.get('output_tokens', 0)}, "
        f"urls_fetched={len(notes.get('urls_fetched', []))}"
    )


async def parse_models_from_docs(
    seed_urls: list[str],
    provider_id: str,
    model_type: Literal["stt", "tts"],
    guidance: str = "",
    api_key: str | None = None,
) -> tuple[list[SyncModel], dict]:
    """
    Extract models from documentation — cache-first, then Anthropic API.

    Claude Code path (no ANTHROPIC_API_KEY):
      Checks .sync-cache/{provider_id}_{model_type}_models.json first.
      If found, returns cached models without any API call.
      If not found, raises AIParserError with instructions for Claude Code.

    Admin UI path (ANTHROPIC_API_KEY set):
      Runs the agentic Claude loop directly — cache is ignored.

    Returns (models, notes) where notes contains provenance for SyncResult.notes.
    Syncers that pass the raw dict into SyncResult.notes must stringify it first —
    sync_runs.notes is VARCHAR.
    """
    if not seed_urls:
        raise AIParserError("seed_urls must contain at least one URL")

    # Claude Code path: check cache before hitting the API
    if not (api_key or os.getenv("ANTHROPIC_API_KEY")):
        from naaviq.sync.cache import read_models_cache, _models_path
        cached = read_models_cache(provider_id, model_type)
        if cached is not None:
            return cached, {"source": "cache", "path": str(_models_path(provider_id, model_type))}
        raise AIParserError(
            f"ANTHROPIC_API_KEY not set and no cache found for {provider_id} {model_type} models.\n"
            f"  Cache path: .sync-cache/{provider_id}_{model_type}_models.json\n"
            f"  Docs URLs : {seed_urls}\n"
            f"  To fix    : Ask Claude Code to extract {model_type} models for {provider_id} "
            f"from the docs URLs above and write them to the cache path."
        )

    client = _make_client(api_key)

    user_msg = (
        f"Provider: {provider_id}\n"
        f"Model type to extract: {model_type}\n"
        f"Seed URLs (fetch ALL of these to start):\n"
        + "\n".join(f"  - {url}" for url in seed_urls)
        + "\n"
    )
    if guidance:
        user_msg += f"\nGuidance: {guidance}\n"
    user_msg += (
        "\nFetch every seed URL listed above. Follow additional links from those "
        "pages only if you need more information. Then call return_models once."
    )

    messages: list[dict] = [{"role": "user", "content": user_msg}]

    terminal_input, urls_fetched, input_tokens, output_tokens = await _run_agentic_loop(
        client=client,
        system_prompt=_MODELS_SYSTEM_PROMPT,
        tools=_MODELS_TOOLS,
        messages=messages,
        terminal_tool_name="return_models",
    )

    try:
        raw_models = terminal_input.get("models", [])
        models = [_to_sync_model(m) for m in raw_models]
    except (KeyError, TypeError, ValueError) as e:
        raise AIParserError(f"Invalid return_models payload: {e}") from e

    models = [m for m in models if m.type == model_type]

    notes = {
        "urls_fetched": urls_fetched,
        "model": MODEL,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    return models, notes


async def parse_voices_from_docs(
    seed_urls: list[str],
    provider_id: str,
    guidance: str = "",
    api_key: str | None = None,
) -> tuple[list[SyncVoice], dict]:
    """
    Extract voices from documentation — cache-first, then Anthropic API.

    Claude Code path (no ANTHROPIC_API_KEY):
      Checks .sync-cache/{provider_id}_voices.json first.
      If found, returns cached voices without any API call.
      If not found, raises AIParserError with instructions for Claude Code.

    Admin UI path (ANTHROPIC_API_KEY set):
      Runs the agentic Claude loop directly — cache is ignored.
    """
    if not seed_urls:
        raise AIParserError("seed_urls must contain at least one URL")

    # Claude Code path: check cache before hitting the API
    if not (api_key or os.getenv("ANTHROPIC_API_KEY")):
        from naaviq.sync.cache import read_voices_cache, _voices_path
        cached = read_voices_cache(provider_id)
        if cached is not None:
            return cached, {"source": "cache", "path": str(_voices_path(provider_id))}
        raise AIParserError(
            f"ANTHROPIC_API_KEY not set and no cache found for {provider_id} voices.\n"
            f"  Cache path: .sync-cache/{provider_id}_voices.json\n"
            f"  Docs URLs : {seed_urls}\n"
            f"  To fix    : Ask Claude Code to extract voices for {provider_id} "
            f"from the docs URLs above and write them to the cache path."
        )

    client = _make_client(api_key)

    user_msg = (
        f"Provider: {provider_id}\n"
        f"Seed URLs (fetch ALL of these to start):\n"
        + "\n".join(f"  - {url}" for url in seed_urls)
        + "\n"
    )
    if guidance:
        user_msg += f"\nGuidance: {guidance}\n"
    user_msg += (
        "\nFetch every seed URL listed above. Follow additional links from those "
        "pages only if you need more information. Then call return_voices once."
    )

    messages: list[dict] = [{"role": "user", "content": user_msg}]

    terminal_input, urls_fetched, input_tokens, output_tokens = await _run_agentic_loop(
        client=client,
        system_prompt=_VOICES_SYSTEM_PROMPT,
        tools=_VOICES_TOOLS,
        messages=messages,
        terminal_tool_name="return_voices",
    )

    try:
        raw_voices = terminal_input.get("voices", [])
        voices = [_to_sync_voice(v) for v in raw_voices]
    except (KeyError, TypeError, ValueError) as e:
        raise AIParserError(f"Invalid return_voices payload: {e}") from e

    notes = {
        "urls_fetched": urls_fetched,
        "model": MODEL,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    return voices, notes


# ── Core agentic loop ──────────────────────────────────────────────────────────

async def _run_agentic_loop(
    *,
    client: AsyncAnthropic,
    system_prompt: str,
    tools: list[dict],
    messages: list[dict],
    terminal_tool_name: str,
) -> tuple[dict, list[str], int, int]:
    """
    Drive the Claude tool-use loop until the terminal tool is called.

    Returns (terminal_tool_input, urls_fetched, input_tokens, output_tokens).
    Raises AIParserError on any failure (auth, rate limit, max iterations, etc.).
    """
    urls_fetched: list[str] = []
    input_tokens = 0
    output_tokens = 0

    # One httpx client for the whole run — avoids a fresh TLS handshake per fetch.
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as http_client:
        for iteration in range(MAX_ITERATIONS):
            try:
                response = await client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS_PER_TURN,
                    temperature=0,
                    system=[
                        {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}},
                    ],
                    tools=tools,
                    messages=messages,
                )
            except AuthenticationError as e:
                raise AIParserError(
                    "Anthropic authentication failed — check ANTHROPIC_API_KEY is set and valid."
                ) from e
            except BadRequestError as e:
                msg = str(e)
                if "credit balance" in msg.lower():
                    raise AIParserError(
                        "Anthropic API credit balance is too low. Top up at "
                        "console.anthropic.com → Plans & Billing (this is a separate bucket from claude.ai credits)."
                    ) from e
                raise AIParserError(f"Anthropic API rejected the request: {msg}") from e
            except RateLimitError as e:
                raise AIParserError("Anthropic rate limit hit — try again in a moment.") from e
            except APIConnectionError as e:
                raise AIParserError(f"Cannot reach Anthropic API: {e}") from e
            except APIStatusError as e:
                raise AIParserError(f"Anthropic API error ({e.status_code}): {e}") from e

            input_tokens += response.usage.input_tokens
            output_tokens += response.usage.output_tokens

            terminal_block = next(
                (b for b in response.content if b.type == "tool_use" and b.name == terminal_tool_name),
                None,
            )
            if terminal_block:
                return terminal_block.input, urls_fetched, input_tokens, output_tokens

            if response.stop_reason != "tool_use":
                raise AIParserError(
                    f"Claude stopped without calling {terminal_tool_name} "
                    f"(stop_reason={response.stop_reason})"
                )

            # Respond to every tool_use block (API rejects partial responses)
            tool_results: list[dict] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if block.name == "fetch_url":
                    url = block.input.get("url", "")
                    if len(urls_fetched) >= MAX_URLS:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": (
                                f"Error: max {MAX_URLS} URLs already fetched. "
                                f"Call {terminal_tool_name} with what you have."
                            ),
                            "is_error": True,
                        })
                        continue
                    try:
                        text = await _fetch_text(http_client, url)
                        urls_fetched.append(url)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": text,
                        })
                    except httpx.HTTPError as e:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Error fetching {url}: {e}",
                            "is_error": True,
                        })
                else:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Unknown tool: {block.name}",
                        "is_error": True,
                    })

            messages.append({"role": "assistant", "content": response.content})

            remaining = MAX_ITERATIONS - iteration - 1
            if 0 < remaining <= NUDGE_THRESHOLD:
                tool_results.append({
                    "type": "text",
                    "text": (
                        f"You have {remaining} iteration(s) left before this run aborts. "
                        f"Stop fetching and call {terminal_tool_name} with whatever you have now."
                    ),
                })
            messages.append({"role": "user", "content": tool_results})

    raise AIParserError(
        f"Hit max iterations ({MAX_ITERATIONS}) without {terminal_tool_name}. "
        f"URLs fetched: {urls_fetched}"
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_client(api_key: str | None) -> AsyncAnthropic:
    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise AIParserError(
            "ANTHROPIC_API_KEY not set.\n"
            "  Admin UI path : set ANTHROPIC_API_KEY in .env\n"
            "  Claude Code   : extract models/voices manually and write to .sync-cache/"
        )
    return AsyncAnthropic(api_key=key)


async def _fetch_text(http_client: httpx.AsyncClient, url: str) -> str:
    resp = await http_client.get(url, headers={"User-Agent": "naaviq-sync/1.0"})
    resp.raise_for_status()
    text = _html_to_text(resp.text)
    if len(text) > MAX_PAGE_CHARS:
        text = text[:MAX_PAGE_CHARS] + "\n\n[truncated — follow links for more]"
    return text


def _html_to_text(html: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(html)
    raw = "".join(extractor.parts)
    lines = (line.strip() for line in raw.splitlines())
    return "\n".join(line for line in lines if line)


class _TextExtractor(HTMLParser):
    _SKIP_TAGS = {"script", "style", "nav", "footer", "header", "noscript"}
    _BLOCK_TAGS = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, _attrs: list) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)


def _to_sync_model(d: dict) -> SyncModel:
    return SyncModel(
        model_id=d["model_id"],
        display_name=d["display_name"],
        type=d["type"],
        languages=normalize_languages(d.get("languages") or []),
        streaming=d.get("streaming", True),
        is_default=d.get("is_default", False),
        lifecycle=d.get("lifecycle") or "ga",
        description=d.get("description"),
        eol_date=d.get("eol_date"),
        sample_rates_hz=d.get("sample_rates_hz") or [],
        audio_formats=d.get("audio_formats") or [],
        max_text_chars=d.get("max_text_chars"),
        max_audio_seconds=d.get("max_audio_seconds"),
        capabilities=d.get("capabilities") or [],
        regions=d.get("regions") or [],
        pricing=d.get("pricing") or {},
        meta=d.get("meta") or {},
    )


def _to_sync_voice(d: dict) -> SyncVoice:
    return SyncVoice(
        voice_id=d["voice_id"],
        display_name=d["display_name"],
        gender=d.get("gender"),
        category=d.get("category", "premade"),
        languages=normalize_languages(d.get("languages") or []),
        description=d.get("description"),
        preview_url=d.get("preview_url"),
        accent=d.get("accent"),
        age=d.get("age"),
        use_cases=d.get("use_cases") or [],
        tags=d.get("tags") or [],
        compatible_models=d.get("compatible_models") or [],
        capabilities=d.get("capabilities") or [],
        meta=d.get("meta") or {},
    )


# ── Local runner ──────────────────────────────────────────────────────────────

async def _main() -> None:
    """Smoke test against Cartesia's TTS models docs pages."""
    try:
        models, notes = await parse_models_from_docs(
            seed_urls=[
                "https://docs.cartesia.ai/build-with-cartesia/tts-models/latest",
                "https://docs.cartesia.ai/build-with-cartesia/tts-models/older-models",
            ],
            provider_id="cartesia",
            model_type="tts",
            guidance="The latest sonic version is the recommended default.",
        )
    except AIParserError as e:
        print(f"\nError: {e}", file=sys.stderr)
        raise SystemExit(1)

    print(f"\n=== Models ({len(models)}) ===")
    for m in models:
        marker = " [default]" if m.is_default else ""
        print(f"  {m.model_id!r:20} {m.display_name!r:25} langs={len(m.languages)}{marker}")
        if m.description:
            print(f"    {m.description}")

    print("\n=== Notes ===")
    for k, v in notes.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(_main())
