"""Tests for _SESSION_TAG_RE compact display-tag extraction."""

from __future__ import annotations

from cortex_store.routes.assertions._shared import _SESSION_TAG_RE


def test_session_tag_address_vocabulary_web_anthropic() -> None:
    evidence = "Note from session web-anthropic-2026-07-10-033013-0b2"
    m = _SESSION_TAG_RE.search(evidence)
    assert m is not None
    assert m.group() == "web-anthropic-2026-07-10-033013"


def test_session_tag_address_vocabulary_cursor() -> None:
    evidence = "Follow-up on cursor-2026-06-10-012830-abc"
    m = _SESSION_TAG_RE.search(evidence)
    assert m is not None
    assert m.group() == "cursor-2026-06-10-012830"


def test_session_tag_address_vocabulary_api_openai() -> None:
    evidence = "api-openai-2026-05-17-045830"
    m = _SESSION_TAG_RE.search(evidence)
    assert m is not None
    assert m.group() == "api-openai-2026-05-17-045830"


def test_session_tag_legacy_web_short_form() -> None:
    evidence = "Your Notes [web-2026-06-09-2318] claim text"
    m = _SESSION_TAG_RE.search(evidence)
    assert m is not None
    assert m.group() == "web-2026-06-09-2318"


def test_session_tag_pattern_prefers_provider_scoped_alternative() -> None:
    """(?:web|api)-[a-z]+ must be first alternative — longest match wins."""
    pattern = _SESSION_TAG_RE.pattern
    assert pattern.index("(?:web|api)-[a-z]+") < pattern.index("cursor|web|api|bard")
