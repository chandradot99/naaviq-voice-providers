"""
AI-powered documentation parser using Claude with tool use.

Used by sync scripts when a provider doesn't expose a REST API for models —
Claude fetches the docs page, follows links as needed, and returns structured
SyncModel objects via a terminal `return_models` tool call.

Safety guards:
  - Max 10 iterations per parse call
  - Max 60,000 chars per fetched page (truncated with marker)
  - Max 15 distinct URLs fetched per call
  - Invalid `return_models` payload raises AIParserError (no partial data —
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

from naaviq.sync.base import SyncModel
from naaviq.sync.language import normalize_languages

MODEL = "claude-sonnet-4-6"
MAX_ITERATIONS = 15
MAX_PAGE_CHARS = 60_000
MAX_URLS = 15
MAX_TOKENS_PER_TURN = 8000

# When this many iterations remain, append a nudge to every tool_result asking
# Claude to wrap up and call return_models. Stops the model from infinite-fetching.
NUDGE_THRESHOLD = 3


class AIParserError(Exception):
    pass


SYSTEM_PROMPT = """You are a documentation parser for an open-source voice provider registry.

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
  - description (str | omit)
  - meta (object)                    — provider-specific extras that don't fit elsewhere

Rules:
  1. Don't invent fields. If docs don't say, omit (or use the default).
  2. BCP-47 with uppercase region. "*" only when docs explicitly say multilingual.
  3. Mark exactly ONE model as is_default per type when docs indicate a default/latest/recommended.
  4. Follow links from the seed page only when the seed lacks the data.
  5. When you have all models, call return_models ONCE. Don't call it twice.
  6. If a page is truncated, follow links to subpages instead of guessing.
"""


TOOLS = [
    {
        "name": "fetch_url",
        "description": "Fetch a URL and return its text content. Use this to read documentation pages and follow links.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute URL to fetch"},
            },
            "required": ["url"],
        },
    },
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
                            "description":  {"type": "string"},
                            "meta":         {"type": "object"},
                        },
                        "required": ["model_id", "display_name", "type"],
                    },
                },
            },
            "required": ["models"],
        },
    },
]


async def parse_models_from_docs(
    seed_urls: list[str],
    provider_id: str,
    model_type: Literal["stt", "tts"],
    guidance: str = "",
    api_key: str | None = None,
) -> tuple[list[SyncModel], dict]:
    """
    Run an agentic Claude loop to extract models from documentation.

    `seed_urls` is one or more pages Claude should fetch up front. Claude can
    follow additional links from those pages via the fetch_url tool if needed.

    Returns (models, notes) where notes contains provenance for SyncResult.notes:
      {urls_fetched: list[str], model: str, input_tokens: int, output_tokens: int}
    """
    if not seed_urls:
        raise AIParserError("seed_urls must contain at least one URL")

    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise AIParserError("ANTHROPIC_API_KEY not set")

    client = AsyncAnthropic(api_key=key)

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
    urls_fetched: list[str] = []
    input_tokens = 0
    output_tokens = 0

    for iteration in range(MAX_ITERATIONS):
        try:
            response = await client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS_PER_TURN,
                temperature=0,
                system=[
                    {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
                ],
                tools=TOOLS,
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

        # Terminal: did Claude call return_models?
        return_block = next(
            (b for b in response.content if b.type == "tool_use" and b.name == "return_models"),
            None,
        )
        if return_block:
            try:
                raw_models = return_block.input.get("models", [])
                models = [_to_sync_model(m) for m in raw_models]
            except (KeyError, TypeError, ValueError) as e:
                raise AIParserError(f"Invalid return_models payload: {e}") from e

            # Defensive: filter to the requested type
            models = [m for m in models if m.type == model_type]

            notes = {
                "urls_fetched": urls_fetched,
                "model": MODEL,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }
            return models, notes

        if response.stop_reason != "tool_use":
            raise AIParserError(
                f"Claude stopped without calling return_models (stop_reason={response.stop_reason})"
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
                        "content": f"Error: max {MAX_URLS} URLs already fetched. Call return_models with what you have.",
                        "is_error": True,
                    })
                    continue
                try:
                    text = await _fetch_text(url)
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
                    "Stop fetching and call return_models with whatever you have now."
                ),
            })
        messages.append({"role": "user", "content": tool_results})

    raise AIParserError(
        f"Hit max iterations ({MAX_ITERATIONS}) without return_models. URLs fetched: {urls_fetched}"
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _fetch_text(url: str) -> str:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "naaviq-sync/1.0"})
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
        description=d.get("description"),
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
