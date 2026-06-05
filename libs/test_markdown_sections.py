"""Tests for markdown_sections resolution — focus on slash-in-heading support.

Regression: ``resolve_section`` previously gated its heading-name fallback
behind ``"/" not in normalized``, so a heading containing a literal slash
(e.g. "Fences C / D") was unreachable by any natural query — only the fully
qualified ``\\/``-escaped path matched. The matcher now accepts the natural
heading verbatim because ``parse_sections`` stores the unescaped heading text.
"""

from __future__ import annotations

import pytest
from markdown_sections import (
    SectionError,
    append_section,
    parse_sections,
    read_section,
    replace_section,
    resolve_section,
)

_DOC = """# Top

preamble-ish

## Fences C / D — PENDING
slash body

## Fence E — done
plain body

### Captured behavior (key cases)
nested body
"""


def test_parse_escapes_slash_in_path_but_keeps_heading_natural() -> None:
    by_heading = {s.heading: s for s in parse_sections(_DOC) if s.level}
    sec = by_heading["Fences C / D — PENDING"]
    assert sec.path == "Top/Fences C \\/ D — PENDING"


def test_natural_slash_heading_resolves() -> None:
    sec = resolve_section(_DOC, "Fences C / D — PENDING")
    assert sec.heading == "Fences C / D — PENDING"
    assert read_section(_DOC, "Fences C / D — PENDING").strip() == "slash body"


def test_escaped_slash_leaf_resolves_to_same_section() -> None:
    sec = resolve_section(_DOC, "Fences C \\/ D — PENDING")
    assert sec.heading == "Fences C / D — PENDING"


def test_fully_qualified_escaped_path_still_resolves() -> None:
    sec = resolve_section(_DOC, "Top/Fences C \\/ D — PENDING")
    assert sec.heading == "Fences C / D — PENDING"


def test_append_to_slash_heading_uses_natural_query() -> None:
    updated = append_section(_DOC, "Fences C / D — PENDING", "appended line\n")
    assert "slash body" in updated
    assert "appended line" in updated
    # The following sibling section is untouched.
    assert "plain body" in updated


def test_replace_slash_heading_body() -> None:
    updated = replace_section(_DOC, "Fences C / D — PENDING", "NEW BODY\n")
    assert "slash body" not in updated
    assert "NEW BODY" in updated


def test_plain_leaf_heading_still_resolves() -> None:
    assert resolve_section(_DOC, "Fence E — done").level == 2


def test_nested_bare_leaf_suffix_match_preserved() -> None:
    sec = resolve_section(_DOC, "Captured behavior (key cases)")
    assert sec.level == 3


def test_empty_query_selects_preamble() -> None:
    assert resolve_section(_DOC, "").level == 0


def test_unknown_section_raises() -> None:
    with pytest.raises(SectionError, match="Section not found"):
        resolve_section(_DOC, "No Such Heading")


def test_ambiguous_duplicate_heading_raises() -> None:
    doc = "## Dup / Slash\na\n\n## Other\nb\n\n### Dup / Slash\nc\n"
    with pytest.raises(SectionError, match="Ambiguous heading"):
        resolve_section(doc, "Dup / Slash")
