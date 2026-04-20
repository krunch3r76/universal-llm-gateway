"""Unit tests for ``detect_output_short``."""

from __future__ import annotations

from frontier_observability import detect_output_short
from frontier_observability.output_short import (
    CONTENT_PREVIEW_CHAR_LIMIT,
    SHORT_OUTPUT_TOKEN_THRESHOLD,
)


def _call(**overrides: object) -> object:
    base: dict[str, object] = {
        "boot_level": "team",
        "output_tokens": 42,
        "tool_calls_made": 0,
        "finish_reason": "end_turn",
        "block_reason": None,
        "content": "short but content",
    }
    base.update(overrides)
    return detect_output_short(**base)  # type: ignore[arg-type]


def test_fires_on_team_boot_below_threshold() -> None:
    payload = _call(boot_level="team", output_tokens=100, content="hi there")
    assert payload is not None
    assert payload.boot_level == "team"
    assert payload.output_tokens == 100
    assert payload.content_preview == "hi there"


def test_fires_on_full_boot_below_threshold() -> None:
    payload = _call(boot_level="full", output_tokens=0)
    assert payload is not None
    assert payload.boot_level == "full"


def test_skips_persona_free_boot_levels() -> None:
    assert _call(boot_level="none", output_tokens=10) is None
    assert _call(boot_level="mcp", output_tokens=10) is None
    assert _call(boot_level="light", output_tokens=10) is None


def test_skips_when_at_or_above_threshold() -> None:
    assert _call(output_tokens=SHORT_OUTPUT_TOKEN_THRESHOLD) is None
    assert _call(output_tokens=SHORT_OUTPUT_TOKEN_THRESHOLD + 1) is None


def test_content_preview_truncated() -> None:
    big = "x" * (CONTENT_PREVIEW_CHAR_LIMIT * 2)
    payload = _call(content=big)
    assert payload is not None
    assert len(payload.content_preview) == CONTENT_PREVIEW_CHAR_LIMIT


def test_content_none_preview_is_empty_string() -> None:
    payload = _call(content=None)
    assert payload is not None
    assert payload.content_preview == ""


def test_as_dict_contains_expected_keys() -> None:
    payload = _call(output_tokens=5)
    assert payload is not None
    d = payload.as_dict()
    assert set(d.keys()) == {
        "boot_level",
        "output_tokens",
        "tool_calls_made",
        "finish_reason",
        "block_reason",
        "content_preview",
    }
