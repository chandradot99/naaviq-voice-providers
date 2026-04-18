"""
JSON cache for AI-extracted models — used by the Claude Code sync path.

When ANTHROPIC_API_KEY is not set, parse_models_from_docs and
parse_voices_from_docs check here before raising an error.

Claude Code writes these files after extracting from docs itself.
The sync scripts read them transparently — no code change needed in syncers.

Cache location: .sync-cache/{provider_id}_{model_type}_models.json
                .sync-cache/{provider_id}_voices.json

JSON format matches SyncModel / SyncVoice dataclass fields exactly.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from naaviq.sync.base import SyncModel, SyncVoice
from naaviq.sync.language import normalize_languages

_CACHE_DIR = Path(".sync-cache")


# ── Read ────────────────────────────────────────────────────���─────────────────

def read_models_cache(provider_id: str, model_type: Literal["stt", "tts"]) -> list[SyncModel] | None:
    path = _models_path(provider_id, model_type)
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    return [_model_from_dict(m) for m in data]


def read_voices_cache(provider_id: str) -> list[SyncVoice] | None:
    path = _voices_path(provider_id)
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    return [_voice_from_dict(v) for v in data]


# ── Write ─────────────────────────────────────────────────────────────────────

def write_models_cache(provider_id: str, model_type: Literal["stt", "tts"], models: list[SyncModel]) -> Path:
    _CACHE_DIR.mkdir(exist_ok=True)
    path = _models_path(provider_id, model_type)
    with open(path, "w") as f:
        json.dump([asdict(m) for m in models], f, indent=2, default=str)
    return path


def write_voices_cache(provider_id: str, voices: list[SyncVoice]) -> Path:
    _CACHE_DIR.mkdir(exist_ok=True)
    path = _voices_path(provider_id)
    with open(path, "w") as f:
        json.dump([asdict(v) for v in voices], f, indent=2, default=str)
    return path


# ── Paths ─────────────────────────────���───────────────────────────���───────────

def _models_path(provider_id: str, model_type: str) -> Path:
    return _CACHE_DIR / f"{provider_id}_{model_type}_models.json"


def _voices_path(provider_id: str) -> Path:
    return _CACHE_DIR / f"{provider_id}_voices.json"


# ── Deserializers ───────────────────────────��─────────────────────────────────

def _model_from_dict(d: dict) -> SyncModel:
    return SyncModel(
        model_id=d["model_id"],
        display_name=d["display_name"],
        type=d["type"],
        languages=normalize_languages(d.get("languages") or []),
        streaming=d.get("streaming", True),
        is_default=d.get("is_default", False),
        description=d.get("description"),
        eol_date=d.get("eol_date"),
        meta=d.get("meta") or {},
    )


def _voice_from_dict(d: dict) -> SyncVoice:
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
        meta=d.get("meta") or {},
    )
