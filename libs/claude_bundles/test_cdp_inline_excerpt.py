"""Unit tests for CDP inline skill excerpts (friction a:27142)."""

from __future__ import annotations

import pytest

from claude_bundles.cdp_inline_excerpt import (
    CDP_INLINE_SKILL_MAX_CHARS,
    excerpt_skill_body,
)


def test_excerpt_passthrough_when_under_budget() -> None:
    body = "---\nname: tiny\n---\n\n# Hello\n"
    assert excerpt_skill_body(body, slug="tiny") == body


def test_excerpt_truncates_and_keeps_frontmatter() -> None:
    front = "---\nname: fat\ndescription: x\n---\n\n"
    fat = front + ("line\n" * 2000)
    out = excerpt_skill_body(fat, slug="fat", max_chars=500)
    assert len(out) <= 500
    assert out.startswith("---\nname: fat\n")
    assert "truncated CDP inline excerpt for fat" in out


def test_excerpt_rejects_nonpositive_budget() -> None:
    with pytest.raises(ValueError, match="max_chars"):
        excerpt_skill_body("x", slug="x", max_chars=0)


def test_default_budget_constant() -> None:
    assert CDP_INLINE_SKILL_MAX_CHARS == 6000
