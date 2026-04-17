"""
Language code normalization utilities.

All languages in the registry are stored as BCP-47 with uppercase region suffix:
  "en-us"  → "en-US"
  "es-mx"  → "es-MX"
  "hi-IN"  → "hi-IN"  (already correct)
  "en"     → "en"     (bare code, no region)
  "*"      → "*"      (wildcard, pass-through)

Provider-specific quirks handled:
  - Deepgram  : lowercase BCP-47 ("en-us", "fr-fr")
  - Cartesia  : ISO 639-1 two-letter ("en", "fr")
  - ElevenLabs: ISO 639-1 two-letter ("en", "hu")
  - OpenAI    : ISO 639-1 two-letter ("en", "fr")
  - Sarvam    : BCP-47 uppercase ("hi-IN", "en-IN") — already correct
"""

from __future__ import annotations


def normalize_language(lang: str) -> str:
    """
    Normalize any provider language string to BCP-47 with uppercase region.

    Examples:
        "en-us"   → "en-US"
        "fr-fr"   → "fr-FR"
        "hi-IN"   → "hi-IN"
        "en"      → "en"
        "*"       → "*"
        "zh-hans" → "zh-Hans"   (script subtag, title-cased)
    """
    if not lang or lang == "*":
        return lang

    parts = lang.replace("_", "-").split("-")

    if len(parts) == 1:
        return parts[0].lower()

    if len(parts) == 2:
        # Could be "en-US" (language-region) or "zh-Hans" (language-script)
        # Region codes are always 2 uppercase letters; script codes are 4 letters title-cased.
        lang_code = parts[0].lower()
        subtag = parts[1]
        if len(subtag) == 2:
            return f"{lang_code}-{subtag.upper()}"     # "en-us" → "en-US"
        else:
            return f"{lang_code}-{subtag.title()}"     # "zh-hans" → "zh-Hans"

    if len(parts) == 3:
        # e.g. "zh-Hant-TW"
        return f"{parts[0].lower()}-{parts[1].title()}-{parts[2].upper()}"

    return lang


def normalize_languages(langs: list[str]) -> list[str]:
    """Normalize a list of language codes."""
    return [normalize_language(lang) for lang in langs]


# BCP-47 region → accent label, shared across syncers that derive accent from voice language.
ACCENT_MAP: dict[str, str] = {
    "GB": "british",
    "US": "american",
    "AU": "australian",
    "IN": "indian",
    "CA": "canadian",
    "IE": "irish",
    "ZA": "south_african",
    "NZ": "new_zealander",
}


def accent_from_languages(languages: list[str]) -> str | None:
    """Derive accent from BCP-47 region code. e.g., 'en-GB' → 'british'."""
    for lang in languages:
        parts = lang.split("-")
        if len(parts) >= 2 and parts[1].upper() in ACCENT_MAP:
            return ACCENT_MAP[parts[1].upper()]
    return None
