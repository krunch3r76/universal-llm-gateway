"""ATX markdown sections: parse, read/replace/append/delete, dict↔md (stdlib only).

Navigation merges ATX headings with line-anchored XML blocks (handoff packets).
Limitations: headings in blockquotes/lists count; simplified fences."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")


class SectionError(Exception):
    """Invalid section path or ambiguous match."""


@dataclass(slots=True, kw_only=True)
class Section:
    """Section with char offsets; `start` is after heading line, `end` exclusive."""

    heading: str
    level: int
    path: str
    line: int
    start: int
    end: int
    chars: int = 0


def _char_upto(lines: list[str], line_idx: int) -> int:
    return sum(len(lines[k]) for k in range(line_idx))


def _document_sections(text: str) -> list[Section]:
    """ATX sections plus XML block sections sorted by document offset."""
    atx = parse_sections(text)
    from markdown_xml_blocks import parse_xml_block_sections

    xml = parse_xml_block_sections(text)
    if not xml:
        return atx
    preamble = atx[0] if atx and atx[0].level == 0 else None
    body = [
        sec
        for sec in atx
        if sec.level > 0
        and not any(
            sec.start >= xml_sec.start and sec.end <= xml_sec.end for xml_sec in xml
        )
    ] + xml
    body.sort(key=lambda sec: sec.start)
    if preamble is not None:
        return [preamble, *body]
    return body if body else atx


def parse_sections(text: str) -> list[Section]:
    """Flat sections in document order; preamble is level 0 with empty path/heading."""
    lines = text.splitlines(keepends=True)
    sections: list[Section] = []
    in_fence = False
    char_offset = 0
    path_stack: list[tuple[int, str]] = []

    for line_idx, line in enumerate(lines):
        stripped = line.rstrip("\n\r")
        if _FENCE_RE.match(stripped):
            in_fence = not in_fence
            char_offset += len(line)
            continue
        if in_fence:
            char_offset += len(line)
            continue
        m = _HEADING_RE.match(stripped)
        if m:
            level = len(m.group(1))
            heading_text = m.group(2).strip()
            while path_stack and path_stack[-1][0] >= level:
                path_stack.pop()
            esc = heading_text.replace("/", "\\/")
            section_path = (
                f"{'/'.join(s for _, s in path_stack)}/{esc}" if path_stack else esc
            )
            path_stack.append((level, esc))
            nl = text.find("\n", char_offset)
            content_start = nl + 1 if nl != -1 else len(text)
            sections.append(
                Section(
                    heading=heading_text,
                    level=level,
                    path=section_path,
                    line=line_idx + 1,
                    start=content_start,
                    end=len(text),
                )
            )
        char_offset += len(line)

    for i, sec in enumerate(sections):
        for j in range(i + 1, len(sections)):
            if sections[j].level <= sec.level:
                sec.end = _char_upto(lines, sections[j].line - 1)
                break

    preamble_end = _char_upto(lines, sections[0].line - 1) if sections else len(text)
    sections.insert(
        0,
        Section(heading="", level=0, path="", line=1, start=0, end=preamble_end),
    )
    for sec in sections:
        sec.chars = max(0, sec.end - sec.start)
    return sections


def resolve_section(text: str, section_path: str) -> Section:
    """Resolve by full path, suffix, heading, or XML block tag."""
    from markdown_xml_blocks import resolve_xml_section

    sections = parse_sections(text)
    if section_path == "":
        for sec in sections:
            if sec.level == 0:
                return sec
        raise SectionError("No preamble found in document")
    normalized = section_path.strip()
    exact = [s for s in sections if s.path == normalized]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise SectionError(
            f"Multiple sections match path {section_path!r}: "
            + ", ".join(repr(s.path) for s in exact)
        )
    # Strategy 1.5: display-path comparison (handles callers passing unescaped full paths)
    display_lookup = normalized.replace("\\/", "/")
    if "/" in display_lookup:
        first_seg = display_lookup.split("/", 1)[0]
        if any(s.heading == first_seg for s in sections if s.level > 0):
            display_matches = [
                s
                for s in sections
                if s.path.replace("\\/", "/") == display_lookup
                or s.path.replace("\\/", "/").endswith(f"/{display_lookup}")
            ]
            if len(display_matches) == 1:
                return display_matches[0]
            if len(display_matches) > 1:
                candidates = ", ".join(repr(s.path) for s in display_matches)
                raise SectionError(
                    f"Ambiguous section — {len(display_matches)} sections match display path"
                    f" {display_lookup!r}: {candidates}"
                )
    # Heading-name match against the UNescaped heading text. Attempted
    # unconditionally (not gated on absence of "/") so a caller may pass the
    # natural heading verbatim — including one that contains a literal slash
    # (``parse_sections`` escapes "/" as "\/" only in the path form; the
    # ``heading`` field keeps the natural text). A "\/"-escaped leaf query
    # collapses to the same key.
    unesc = normalized.replace("\\/", "/")
    heading_matches = [s for s in sections if s.heading == unesc]
    if len(heading_matches) == 1:
        return heading_matches[0]
    if len(heading_matches) > 1:
        raise SectionError(
            f"Ambiguous heading {section_path!r}. "
            f"Full paths: {', '.join(repr(s.path) for s in heading_matches)}"
        )
    # Trailing-path-suffix match — only for queries that are themselves a bare
    # leaf (no "/"), preserving the original separator semantics for
    # multi-segment path queries.
    if "/" not in normalized:
        suffix_matches = [
            s
            for s in sections
            if s.path.endswith(f"/{normalized}") or s.path == normalized
        ]
        if len(suffix_matches) == 1:
            return suffix_matches[0]
        if len(suffix_matches) > 1:
            raise SectionError(
                f"Ambiguous section {section_path!r}. "
                f"Full paths: {', '.join(repr(s.path) for s in suffix_matches)}"
            )
    xml_sec = resolve_xml_section(text, section_path)
    if xml_sec is not None:
        return xml_sec
    raise SectionError(f"Section not found: {section_path!r}")


def list_sections(text: str) -> list[dict[str, str | int]]:
    """Metadata rows for navigation (skips empty preamble)."""
    return [
        {
            "heading": sec.heading if sec.heading else "[Preamble]",
            "level": sec.level,
            "path": sec.path,
            "line": sec.line,
            "chars": sec.chars,
        }
        for sec in _document_sections(text)
        if not (sec.level == 0 and sec.chars == 0)
    ]


def read_section(text: str, section_path: str) -> str:
    sec = resolve_section(text, section_path)
    return text[sec.start : sec.end]


def _strip_leading_heading(content: str, level: int, heading: str) -> tuple[str, bool]:
    """Strip a leading ATX heading from *content* when it matches *level* + *heading*."""
    secs = parse_sections(content)
    headings = [s for s in secs if s.level > 0]
    if not headings:
        return content, False
    first = headings[0]
    lines = content.splitlines(keepends=True)
    h0 = _char_upto(lines, first.line - 1)
    if content[:h0].strip():
        return content, False
    if first.level == level and first.heading == heading:
        return content[first.start :], True
    return content, False


def strip_redundant_leading_heading(content: str, section: Section) -> tuple[str, bool]:
    """Strip a leading ATX heading from *content* when it duplicates *section*."""
    return _strip_leading_heading(content, section.level, section.heading)


def _doc_eol(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _ensure_blank_before(prefix: str, eol: str) -> str:
    if not prefix:
        return prefix
    if not prefix.endswith(eol):
        prefix += eol
    if not prefix.endswith(eol * 2):
        prefix += eol
    return prefix


def _ensure_blank_after(block: str, suffix: str, eol: str) -> str:
    if suffix and not block.endswith(eol * 2):
        block += eol
    return block


def find_duplicate_section_headings(text: str) -> list[dict[str, str | int]]:
    """Detect ghost duplicate headings (empty first, identical sibling follows)."""
    sections = [s for s in parse_sections(text) if s.level > 0]
    findings: list[dict[str, str | int]] = []
    for i in range(len(sections) - 1):
        cur, nxt = sections[i], sections[i + 1]
        if cur.level != nxt.level or cur.heading != nxt.heading:
            continue
        if text[cur.start : cur.end].strip():
            continue
        findings.append({"heading": cur.heading, "level": cur.level, "line": cur.line})
    return findings


def _set_section_body(text: str, sec: Section, body: str) -> str:
    if body and not body.endswith("\n"):
        body += "\n"
    after = text[sec.end :]
    if after and body and not body.endswith("\n\n"):
        body += "\n"
    return text[: sec.start] + body + after


def replace_section(text: str, section_path: str, new_content: str) -> tuple[str, bool]:
    """Replace body only; returns (updated_text, heading_was_normalized)."""
    sec = resolve_section(text, section_path)
    body, normd = strip_redundant_leading_heading(new_content, sec)
    return _set_section_body(text, sec, body), normd


def append_section(text: str, section_path: str, added_content: str) -> tuple[str, bool]:
    """Append to section body; normalizes only *added_content*, not existing body."""
    sec = resolve_section(text, section_path)
    frag, normd = strip_redundant_leading_heading(added_content, sec)
    cur = text[sec.start : sec.end]
    if cur and not cur.endswith("\n"):
        cur += "\n"
    return _set_section_body(text, sec, cur + frag), normd


def insert_section(
    text: str,
    heading: str,
    level: int,
    position: str,
    anchor: str | None = None,
    body: str = "",
) -> tuple[str, bool]:
    """Insert a new ATX section; returns (updated_text, heading_was_normalized)."""
    if not (1 <= level <= 6):
        raise SectionError(f"Invalid level {level}: must be 1-6")
    if position not in ("end", "after", "before"):
        raise SectionError(
            f"Invalid position {position!r}: must be 'end', 'after', or 'before'"
        )
    if position in ("after", "before") and not anchor:
        raise SectionError("anchor section required for position before/after")

    body_n, normalized = _strip_leading_heading(body, level, heading)
    eol = _doc_eol(text)

    block = f"{'#' * level} {heading}{eol}"
    if body_n:
        if eol == "\r\n" and "\r\n" not in body_n and "\n" in body_n:
            body_n = body_n.replace("\n", "\r\n")
        if not body_n.endswith(eol):
            body_n += eol
        block += body_n

    if position == "end":
        offset = len(text)
    elif position == "after":
        sec = resolve_section(text, anchor)
        offset = sec.end
    else:
        sec = resolve_section(text, anchor)
        lines = text.splitlines(keepends=True)
        offset = _char_upto(lines, sec.line - 1)

    prefix = _ensure_blank_before(text[:offset], eol)
    block = _ensure_blank_after(block, text[offset:], eol)
    if not block.endswith(eol):
        block += eol
    return prefix + block + text[offset:], normalized


def delete_section(text: str, section_path: str) -> str:
    """Remove heading line and body (preamble: strip through `end` only)."""
    sec = resolve_section(text, section_path)
    if sec.level == 0:
        return text[sec.end :]
    lines = text.splitlines(keepends=True)
    h0 = _char_upto(lines, sec.line - 1)
    return text[:h0] + text[sec.end :]


def sections_to_dict(text: str) -> dict[str, Any]:
    """Nested dict by heading; `_preamble`; parents with body use `_content`."""
    sections = parse_sections(text)
    root: dict[str, Any] = {}
    preamble = text[sections[0].start : sections[0].end]
    if preamble.strip():
        root["_preamble"] = preamble
    heading_sections = [s for s in sections if s.level > 0]
    if not heading_sections:
        return root if preamble.strip() else {"_preamble": text}
    _build_dict_recursive(root, heading_sections, text, 0, len(heading_sections))
    return root


def _build_dict_recursive(
    target: dict[str, Any],
    sections: list[Section],
    text: str,
    start_idx: int,
    end_idx: int,
) -> None:
    i = start_idx
    all_lines = text.splitlines(keepends=True)
    while i < end_idx:
        sec = sections[i]
        c0, c1 = i + 1, i + 1
        while c1 < end_idx and sections[c1].level > sec.level:
            c1 += 1
        if c0 == c1:
            target[sec.heading] = text[sec.start : sec.end]
        else:
            child: dict[str, Any] = {}
            fc = sections[c0]
            h0 = _char_upto(all_lines, fc.line - 1)
            direct = text[sec.start : h0]
            if direct.strip():
                child["_content"] = direct
            _build_dict_recursive(child, sections, text, c0, c1)
            target[sec.heading] = child
        i = c1


def dict_to_markdown(data: dict[str, Any], *, base_level: int = 1) -> str:
    """`_preamble` before headings; `_content` is parent body before children."""
    parts: list[str] = []
    pre = data.get("_preamble", "")
    if pre:
        parts.append(pre if pre.endswith("\n") else pre + "\n")
    for key, value in data.items():
        if key in ("_preamble", "_content"):
            continue
        _render_key(parts, key, value, base_level)
    result = "".join(parts)
    if result and not result.endswith("\n"):
        result += "\n"
    return result


def _render_key(parts: list[str], key: str, value: Any, level: int) -> None:
    parts.append(f"{'#' * level} {key}\n")
    if isinstance(value, str):
        if value.strip():
            if not value.startswith("\n"):
                parts.append("\n")
            parts.append(value if value.endswith("\n") else value + "\n")
        parts.append("\n")
    elif isinstance(value, dict):
        oc = value.get("_content", "")
        if oc and oc.strip():
            if not oc.startswith("\n"):
                parts.append("\n")
            parts.append(oc if oc.endswith("\n") else oc + "\n")
        parts.append("\n")
        for ck, cv in value.items():
            if ck not in ("_preamble", "_content"):
                _render_key(parts, ck, cv, level + 1)


def dict_from_json(json_str: str) -> dict[str, Any]:
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise SectionError(f"Invalid JSON for from_dict: {e}") from e
    if not isinstance(data, dict):
        raise SectionError(
            f"dict_from_json expects a JSON object, got {type(data).__name__}"
        )
    return data
