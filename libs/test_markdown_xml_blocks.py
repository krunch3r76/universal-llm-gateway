"""Fence-pairing regressions for markdown_xml_blocks (stdlib only)."""

from __future__ import annotations

from markdown_sections import list_sections, parse_sections, read_section
from markdown_xml_blocks import parse_xml_block_sections


def test_outer_scan_xml_inside_fenced_body_ignored() -> None:
    """XML open tag inside a fenced body must not start a block section."""
    doc = (
        "# Doc\n\n"
        "````text\n"
        "<scope>\n"
        "inside fence\n"
        "</scope>\n"
        "````\n\n"
        "<scope>\n"
        "real block\n"
        "</scope>\n"
    )
    xml_secs = parse_xml_block_sections(doc)
    assert len(xml_secs) == 1
    assert xml_secs[0].path == "<scope>"
    assert read_section(doc, "<scope>").strip() == "real block"
    rows = list_sections(doc)
    scope_rows = [r for r in rows if r["path"] == "<scope>"]
    assert len(scope_rows) == 1


def test_inner_close_search_false_fence_does_not_terminate_early() -> None:
    """False fence line inside an open inner fence must not terminate close-search."""
    doc = (
        "<scope>\n"
        "````text\n"
        "```\n"  # bare inner fence — must not flip inner fence state
        "still inside\n"
        "````\n"
        "after inner\n"
        "</scope>\n"
    )
    xml_secs = parse_xml_block_sections(doc)
    assert len(xml_secs) == 1
    body = read_section(doc, "<scope>")
    assert "still inside" in body
    assert "after inner" in body
    assert "</scope>" not in body


def test_cr_smuggled_opener_parsed_via_strip() -> None:
    """CR-smuggled fence opener parses via line.strip() — heading stays suppressed."""
    doc = "# Doc\n\n````text\n~~\r```text\n## Hidden\n````\n"
    headings = [s.heading for s in parse_sections(doc) if s.level > 0]
    assert headings == ["Doc"]
    assert "Hidden" not in headings
