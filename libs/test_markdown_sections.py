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
    delete_section,
    find_duplicate_section_headings,
    insert_section,
    list_sections,
    parse_sections,
    read_section,
    replace_section,
    resolve_section,
    sections_to_dict,
    strip_redundant_leading_heading,
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
    updated, normd = append_section(_DOC, "Fences C / D — PENDING", "appended line\n")
    assert not normd
    assert "slash body" in updated
    assert "appended line" in updated
    # The following sibling section is untouched.
    assert "plain body" in updated


def test_replace_slash_heading_body() -> None:
    updated, normd = replace_section(_DOC, "Fences C / D — PENDING", "NEW BODY\n")
    assert not normd
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


class TestResolveSlashInHeading:
    SLASH_DOC = """# Parent

## densify/consult path

Body of slash heading.

## Other

Other body.

## A/B

Body AB.
"""

    DEEP_DOC = """# Root

## Alpha

### Alpha/Beta

Deep body.

## Alpha/Beta

Shallow body.
"""

    def test_leaf_unescaped(self) -> None:
        body = read_section(self.SLASH_DOC, "densify/consult path")
        assert "Body of slash heading" in body

    def test_leaf_escaped(self) -> None:
        body = read_section(self.SLASH_DOC, r"densify\/consult path")
        assert "Body of slash heading" in body

    def test_full_unescaped(self) -> None:
        body = read_section(self.SLASH_DOC, "Parent/densify/consult path")
        assert "Body of slash heading" in body

    def test_full_escaped(self) -> None:
        body = read_section(self.SLASH_DOC, r"Parent/densify\/consult path")
        assert "Body of slash heading" in body

    def test_no_regression_plain_heading(self) -> None:
        body = read_section(self.SLASH_DOC, "Other")
        assert "Other body" in body

    def test_ambiguous_display_path_raises(self) -> None:
        with pytest.raises(SectionError, match="Ambiguous section"):
            read_section(self.DEEP_DOC, "Alpha/Beta")

    def test_backslash_boundary_documented(self) -> None:
        doc = "# Parent\n\n## foo\\/bar\n\nBody.\n"
        try:
            read_section(doc, r"Parent/foo\/bar")
        except SectionError:
            pass
        except Exception as exc:
            pytest.fail(f"Unexpected exception type: {type(exc).__name__}: {exc}")


class TestHeadingLessContentContract:
    TARGET_DOC = "## Target\n\nexisting body\n\n## Other\nother body\n"

    def test_replace_strips_matching_leading_heading(self) -> None:
        updated, normd = replace_section(
            self.TARGET_DOC, "Target", "## Target\nnew body\n"
        )
        assert normd
        assert find_duplicate_section_headings(updated) == []
        assert updated.count("## Target") == 1
        assert "new body" in updated
        assert "existing body" not in updated

    def test_append_strips_matching_leading_heading(self) -> None:
        updated, normd = append_section(
            self.TARGET_DOC, "Target", "## Target\nappended\n"
        )
        assert normd
        assert find_duplicate_section_headings(updated) == []
        assert updated.count("## Target") == 1
        assert "existing body" in updated
        assert "appended" in updated

    def test_idempotent_double_apply(self) -> None:
        content = "## Target\nbody\n"
        sec = resolve_section(self.TARGET_DOC, "Target")
        once, norm1 = strip_redundant_leading_heading(content, sec)
        twice, norm2 = strip_redundant_leading_heading(once, sec)
        assert norm1
        assert not norm2
        assert once == twice

    def test_not_stripped_different_level(self) -> None:
        updated, normd = replace_section(
            self.TARGET_DOC, "Target", "### Target\nchild body\n"
        )
        assert not normd
        assert "### Target" in updated

    def test_not_stripped_different_text(self) -> None:
        updated, normd = replace_section(
            self.TARGET_DOC, "Target", "## Other Heading\nbody\n"
        )
        assert not normd
        assert "## Other Heading" in updated

    def test_not_stripped_heading_after_content(self) -> None:
        content = "intro line\n## Target\nbody\n"
        updated, normd = replace_section(self.TARGET_DOC, "Target", content)
        assert not normd
        assert content.strip() in updated.replace("\r\n", "\n")

    def test_not_stripped_backtick_fence(self) -> None:
        content = "```\n## Target\n```\nreal body\n"
        updated, normd = replace_section(self.TARGET_DOC, "Target", content)
        assert not normd
        assert "```" in updated

    def test_not_stripped_tilde_fence(self) -> None:
        content = "~~~\n## Target\n~~~\nreal body\n"
        updated, normd = replace_section(self.TARGET_DOC, "Target", content)
        assert not normd
        assert "~~~" in updated

    def test_not_stripped_setext(self) -> None:
        content = "Target\n-----\nbody\n"
        updated, normd = replace_section(self.TARGET_DOC, "Target", content)
        assert not normd
        assert "-----" in updated

    def test_not_stripped_child_heading_under_level2(self) -> None:
        updated, normd = replace_section(
            self.TARGET_DOC, "Target", "### Child\nnested\n"
        )
        assert not normd
        assert "### Child" in updated

    def test_crlf_document(self) -> None:
        doc = "## Target\r\n\r\nbody\r\n\r\n## Other\r\nother\r\n"
        updated, normd = replace_section(doc, "Target", "## Target\r\nnew\r\n")
        assert normd
        assert find_duplicate_section_headings(updated) == []

    def test_eof_no_newline_empty_body(self) -> None:
        doc = "## Target\n\n## Other\nother\n"
        updated, normd = replace_section(doc, "Target", "## Target")
        assert normd
        assert find_duplicate_section_headings(updated) == []
        body = read_section(updated, "Target")
        assert body.strip() == ""

    def test_closing_hash_form_stripped(self) -> None:
        updated, normd = replace_section(
            self.TARGET_DOC, "Target", "## Target ##\nbody\n"
        )
        assert normd
        assert updated.count("## Target") == 1

    def test_indented_atx_not_stripped(self) -> None:
        updated, normd = replace_section(
            self.TARGET_DOC, "Target", "  ## Target\nbody\n"
        )
        assert not normd
        assert "  ## Target" in updated

    def test_level0_preamble_unaffected(self) -> None:
        doc = "preamble text\n\n## Target\nbody\n"
        updated, normd = replace_section(doc, "Target", "## Target\nnew\n")
        assert normd
        assert updated.startswith("preamble text")
        assert find_duplicate_section_headings(updated) == []

    def test_append_preserves_existing_child_heading(self) -> None:
        doc = "## Parent\n\n### Existing Child\nchild body\n\n## Sibling\nx\n"
        updated, normd = append_section(
            doc, "Parent", "## Parent\nmore parent body\n"
        )
        assert normd
        assert updated.count("## Parent") == 1
        assert updated.count("### Existing Child") == 1
        assert "more parent body" in updated

    def test_open_fence_body_boundary(self) -> None:
        doc = "## Target\n\n```python\nopen fence\n"
        frag = "## Target\nappended\n"
        updated, normd = append_section(doc, "Target", frag)
        assert normd
        assert find_duplicate_section_headings(updated) == []
        assert "appended" in updated

    def test_sections_to_dict_round_trip_replace(self) -> None:
        before = sections_to_dict(self.TARGET_DOC)
        updated, _ = replace_section(
            self.TARGET_DOC, "Target", "## Target\nreplaced\n"
        )
        after = sections_to_dict(updated)
        assert set(before) == set(after)
        assert "replaced" in after["Target"]

    def test_sections_to_dict_round_trip_append(self) -> None:
        before = sections_to_dict(self.TARGET_DOC)
        updated, _ = append_section(
            self.TARGET_DOC, "Target", "## Target\nappended\n"
        )
        after = sections_to_dict(updated)
        assert set(before) == set(after)
        assert "appended" in after["Target"]

    def test_clean_doc_no_duplicate_false_positive(self) -> None:
        assert find_duplicate_section_headings(_DOC) == []

    def test_normalized_replace_no_section_drift(self) -> None:
        updated, normd = replace_section(
            self.TARGET_DOC, "Target", "## Target\nfixed body\n"
        )
        assert normd
        assert find_duplicate_section_headings(updated) == []


class TestInsertSection:
    DOC = """# Top

top body

## Alpha

alpha body

### Alpha Child

child body

## Beta

beta body
"""

    def test_insert_at_end(self) -> None:
        updated, normd = insert_section(
            self.DOC, "New Section", 2, "end", body="new body\n"
        )
        assert not normd
        secs = [s for s in parse_sections(updated) if s.level == 2]
        assert secs[-1].heading == "New Section"
        assert "new body" in updated
        assert updated.rstrip().endswith("new body")

    def test_insert_after_subtree_boundary(self) -> None:
        updated, normd = insert_section(
            self.DOC, "After Alpha", 2, "after", anchor="Alpha", body="after alpha\n"
        )
        assert not normd
        alpha_idx = updated.index("## Alpha")
        after_idx = updated.index("## After Alpha")
        beta_idx = updated.index("## Beta")
        child_idx = updated.index("### Alpha Child")
        assert alpha_idx < child_idx < after_idx < beta_idx
        assert "after alpha" in updated

    def test_insert_before_heading(self) -> None:
        updated, normd = insert_section(
            self.DOC, "Before Beta", 2, "before", anchor="Beta", body="before beta\n"
        )
        assert not normd
        before_idx = updated.index("## Before Beta")
        beta_idx = updated.index("## Beta")
        assert before_idx < beta_idx
        assert "before beta" in updated

    def test_body_heading_stripped(self) -> None:
        updated, normd = insert_section(
            self.DOC, "New Section", 2, "end", body="## New Section\nbody\n"
        )
        assert normd
        assert updated.count("## New Section") == 1
        assert find_duplicate_section_headings(updated) == []

    def test_body_unchanged_when_no_leading_heading(self) -> None:
        updated, normd = insert_section(
            self.DOC, "Plain", 2, "end", body="plain body\n"
        )
        assert not normd
        assert "plain body" in updated

    def test_invalid_level(self) -> None:
        with pytest.raises(SectionError, match="Invalid level"):
            insert_section(self.DOC, "X", 0, "end")
        with pytest.raises(SectionError, match="Invalid level"):
            insert_section(self.DOC, "X", 7, "end")

    def test_invalid_position(self) -> None:
        with pytest.raises(SectionError, match="Invalid position"):
            insert_section(self.DOC, "X", 2, "middle")

    def test_missing_anchor_for_after(self) -> None:
        with pytest.raises(SectionError, match="anchor section required"):
            insert_section(self.DOC, "X", 2, "after")

    def test_missing_anchor_for_before(self) -> None:
        with pytest.raises(SectionError, match="anchor section required"):
            insert_section(self.DOC, "X", 2, "before")

    def test_unresolved_anchor(self) -> None:
        with pytest.raises(SectionError, match="Section not found"):
            insert_section(self.DOC, "X", 2, "after", anchor="No Such")

    def test_crlf_document(self) -> None:
        doc = "## Alpha\r\n\r\nbody\r\n\r\n## Beta\r\nbeta\r\n"
        updated, normd = insert_section(
            doc, "New", 2, "end", body="inserted\r\n"
        )
        assert not normd
        assert "\r\n" in updated
        assert "## New\r\n" in updated
        assert "inserted\r\n" in updated

    def test_preamble_only_end(self) -> None:
        doc = "preamble text only\n"
        updated, normd = insert_section(doc, "First", 1, "end", body="section body\n")
        assert not normd
        assert updated.startswith("preamble text only")
        assert "# First" in updated
        assert "section body" in updated

    def test_nested_level_after_anchor(self) -> None:
        updated, normd = insert_section(
            self.DOC, "Deep New", 3, "after", anchor="Alpha", body="deep content\n"
        )
        assert not normd
        sec = resolve_section(updated, "Deep New")
        assert sec.level == 3
        assert "deep content" in read_section(updated, "Deep New")

    def test_sections_to_dict_round_trip(self) -> None:
        updated, _ = insert_section(
            self.DOC, "Dict Test", 2, "end", body="dict body\n"
        )
        data = sections_to_dict(updated)
        assert "Dict Test" in data["Top"]
        assert "dict body" in data["Top"]["Dict Test"]


_SIX_BLOCK_PACKET = """---
contract: implement
---

<scope>
Implement XML navigation.
</scope>

<invariants>
- ATX unchanged
</invariants>

<task_guidance>
## Phase 1
Do the work.
</task_guidance>

<corpus>
- spec.md
</corpus>

<mcp_capabilities>
fs, cortex
</mcp_capabilities>

<output_format>
Sidecar + code.
</output_format>
"""


class TestXmlBlockSections:
    EXPECTED_TAGS = (
        "<scope>",
        "<invariants>",
        "<task_guidance>",
        "<corpus>",
        "<mcp_capabilities>",
        "<output_format>",
    )

    def test_md_list_includes_six_blocks(self) -> None:
        rows = list_sections(_SIX_BLOCK_PACKET)
        xml_rows = [row for row in rows if str(row["path"]).startswith("<")]
        paths = [row["path"] for row in xml_rows]
        assert paths == list(self.EXPECTED_TAGS)

    def test_trailing_inner_atx_excluded_from_nav(self) -> None:
        """ATX headings wholly inside an XML block must not leak into md_list."""
        rows = list_sections(_SIX_BLOCK_PACKET)
        paths = [str(row["path"]) for row in rows]
        assert "<task_guidance>" in paths
        assert "Phase 1" not in paths
        assert not any(not p.startswith("<") and "Phase" in p for p in paths)

    def test_md_read_angle_bracket_key(self) -> None:
        body = read_section(_SIX_BLOCK_PACKET, "<task_guidance>")
        assert "## Phase 1" in body
        assert "Do the work." in body
        assert "<task_guidance>" not in body

    def test_md_read_bare_tag_key(self) -> None:
        body = read_section(_SIX_BLOCK_PACKET, "scope")
        assert "Implement XML navigation." in body

    def test_atx_regression_unchanged(self) -> None:
        atx_rows = list_sections(_DOC)
        assert [row["heading"] for row in atx_rows] == [
            "Top",
            "Fences C / D — PENDING",
            "Fence E — done",
            "Captured behavior (key cases)",
        ]

    def test_xml_tags_inside_fence_ignored(self) -> None:
        doc = "# Doc\n\n```\n<scope>\nfake\n</scope>\n```\n\n<scope>\nreal\n</scope>\n"
        rows = list_sections(doc)
        assert len([r for r in rows if r["path"] == "<scope>"]) == 1
        assert read_section(doc, "<scope>").strip() == "real"

    def test_unknown_xml_section_raises(self) -> None:
        with pytest.raises(SectionError, match="Section not found"):
            read_section(_SIX_BLOCK_PACKET, "<missing_block>")

    def test_xml_mutation_ops_rejected(self) -> None:
        with pytest.raises(SectionError, match="XML block mutation"):
            replace_section(_SIX_BLOCK_PACKET, "<scope>", "nope\n")
        with pytest.raises(SectionError, match="XML block mutation"):
            append_section(_SIX_BLOCK_PACKET, "task_guidance", "nope\n")
        with pytest.raises(SectionError, match="XML block mutation"):
            delete_section(_SIX_BLOCK_PACKET, "<corpus>")


class TestFencePairing:
    """Delimiter-aware fence pairing suppresses ATX headings inside open fences."""

    def test_bare_inner_backtick_fence_does_not_close_outer(self) -> None:
        doc = (
            "# Doc\n\n"
            "````text\n"
            "```\n"  # bare inner fence line — must not toggle outer fence
            "## Leaked Heading\n"
            "````\n"
        )
        headings = [s.heading for s in parse_sections(doc) if s.level > 0]
        assert headings == ["Doc"]
        assert "Leaked Heading" not in headings

    def test_inner_shorter_run_does_not_close_outer(self) -> None:
        doc = (
            "# Doc\n\n"
            "````text\n"
            "~~\n"  # shorter tilde run inside backtick fence
            "## Leaked Heading\n"
            "````\n"
        )
        headings = [s.heading for s in parse_sections(doc) if s.level > 0]
        assert headings == ["Doc"]

    def test_mismatched_fence_char_does_not_close(self) -> None:
        doc = (
            "# Doc\n\n"
            "````text\n"
            "~~~~\n"  # tilde close inside backtick-open fence
            "## Leaked Heading\n"
            "````\n"
        )
        headings = [s.heading for s in parse_sections(doc) if s.level > 0]
        assert headings == ["Doc"]

    def test_longer_close_pin_exact_length_semantics(self) -> None:
        # exact-length close, parity with session_store schema — CommonMark would close here
        doc = (
            "# Doc\n\n"
            "```text\n"
            "body line\n"
            "````\n"  # longer close does not match exact-length need=3
            "# Heading After False Close\n"
            "```\n"
        )
        headings = [s.heading for s in parse_sections(doc) if s.level > 0]
        assert headings == ["Doc"]
        assert "Heading After False Close" not in headings

