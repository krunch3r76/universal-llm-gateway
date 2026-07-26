"""Line-anchored XML block sections for handoff packets (stdlib only)."""

from __future__ import annotations

import re

from markdown_fence import (
    MIN_FENCE_LEN,
    is_closing_fence_line,
    parse_fence_open,
)
from markdown_sections import Section, _char_upto

_XML_TAG_RE = re.compile(r"^<([a-z][a-z0-9_]*)>\s*$")
_XML_CLOSE_RE = re.compile(r"^</([a-z][a-z0-9_]*)>\s*$")

HANDOFF_XML_TAGS: frozenset[str] = frozenset(
    {
        "scope",
        "invariants",
        "task_guidance",
        "corpus",
        "mcp_capabilities",
        "output_format",
    }
)


def _xml_section_key(tag: str) -> str:
    return f"<{tag}>"


def normalize_xml_section_query(section_path: str) -> str | None:
    """Return canonical ``<tag>`` key, or None when not an XML block query."""
    normalized = section_path.strip()
    if not normalized:
        return None
    if normalized.startswith("<") and normalized.endswith(">"):
        inner = normalized[1:-1].strip()
        if re.fullmatch(r"[a-z][a-z0-9_]*", inner):
            return _xml_section_key(inner)
    if re.fullmatch(r"[a-z][a-z0-9_]*", normalized):
        return _xml_section_key(normalized)
    return None


def parse_xml_block_sections(text: str) -> list[Section]:
    """Flat XML block sections in document order (fence-aware)."""
    lines = text.splitlines(keepends=True)
    sections: list[Section] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    line_idx = 0
    while line_idx < len(lines):
        line = lines[line_idx]
        fence_stripped = line.strip()
        if not in_fence:
            parsed = parse_fence_open(fence_stripped)
            if parsed is not None:
                ch, ln, _lang = parsed
                if ln >= MIN_FENCE_LEN:
                    in_fence = True
                    fence_char, fence_len = ch, ln
                    line_idx += 1
                    continue
        elif is_closing_fence_line(fence_stripped, fence_char, fence_len):
            in_fence = False
            fence_char, fence_len = "", 0
            line_idx += 1
            continue
        if in_fence:
            line_idx += 1
            continue
        stripped = line.rstrip("\n\r")
        open_match = _XML_TAG_RE.match(stripped)
        if not open_match:
            line_idx += 1
            continue
        tag = open_match.group(1)
        open_line = line_idx
        content_start = _char_upto(lines, line_idx + 1)
        close_line = line_idx + 1
        found = False
        inner_fence = False
        inner_fence_char = ""
        inner_fence_len = 0
        while close_line < len(lines):
            close_line_text = lines[close_line]
            fence_stripped = close_line_text.strip()
            if not inner_fence:
                parsed = parse_fence_open(fence_stripped)
                if parsed is not None:
                    ch, ln, _lang = parsed
                    if ln >= MIN_FENCE_LEN:
                        inner_fence = True
                        inner_fence_char, inner_fence_len = ch, ln
                        close_line += 1
                        continue
            elif is_closing_fence_line(
                fence_stripped, inner_fence_char, inner_fence_len
            ):
                inner_fence = False
                inner_fence_char, inner_fence_len = "", 0
                close_line += 1
                continue
            if inner_fence:
                close_line += 1
                continue
            close_stripped = close_line_text.rstrip("\n\r")
            close_match = _XML_CLOSE_RE.match(close_stripped)
            if close_match and close_match.group(1) == tag:
                content_end = _char_upto(lines, close_line)
                sections.append(
                    Section(
                        heading=_xml_section_key(tag),
                        level=1,
                        path=_xml_section_key(tag),
                        line=open_line + 1,
                        start=content_start,
                        end=content_end,
                        chars=max(0, content_end - content_start),
                    )
                )
                line_idx = close_line + 1
                found = True
                break
            close_line += 1
        if not found:
            line_idx += 1
    return sections


def resolve_xml_section(text: str, section_path: str) -> Section | None:
    """Resolve one XML block section; None when query is not an XML key."""
    key = normalize_xml_section_query(section_path)
    if key is None:
        return None
    matches = [sec for sec in parse_xml_block_sections(text) if sec.path == key]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        from markdown_sections import SectionError

        raise SectionError(
            f"Multiple XML sections match {section_path!r}: "
            + ", ".join(repr(sec.path) for sec in matches)
        )
    return None
