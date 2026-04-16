"""Tests for BCP-47 language normalization."""

import pytest

from naaviq.sync.language import normalize_language, normalize_languages


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Region gets uppercased
        ("en-us", "en-US"),
        ("fr-fr", "fr-FR"),
        ("hi-in", "hi-IN"),
        # Already uppercase — idempotent
        ("en-US", "en-US"),
        ("hi-IN", "hi-IN"),
        # Bare language code — lowercased
        ("en", "en"),
        ("EN", "en"),
        # Wildcard passes through
        ("*", "*"),
        # Underscore separator normalized to hyphen
        ("en_US", "en-US"),
        ("zh_hans", "zh-Hans"),
        # Script subtag (4 letters) → title-case
        ("zh-hans", "zh-Hans"),
        ("zh-Hans", "zh-Hans"),
        # Three parts: language-script-region
        ("zh-Hant-TW", "zh-Hant-TW"),
        ("zh-hant-tw", "zh-Hant-TW"),
        # Empty string is preserved (caller may want to filter later)
        ("", ""),
    ],
)
def test_normalize_language(raw: str, expected: str) -> None:
    assert normalize_language(raw) == expected


def test_normalize_languages_preserves_order_and_count() -> None:
    out = normalize_languages(["en-us", "fr-fr", "hi-IN", "*"])
    assert out == ["en-US", "fr-FR", "hi-IN", "*"]


def test_normalize_languages_empty_list() -> None:
    assert normalize_languages([]) == []
