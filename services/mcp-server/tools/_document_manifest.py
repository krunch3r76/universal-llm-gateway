"""Document manifest builders for sparse md/PDF reads (slice 1)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from markdown_fence import is_closing_fence_line, parse_fence_open
from markdown_sections import PREAMBLE_ID, _document_sections, parse_sections

from tools._hashing import sha256_hex_equal
from tools._sparse_payload_envelope import _stale_read_root

UNSECTIONED_ID = "__unsectioned__"
MANIFEST_MAX_DESCRIPTORS = 200
MANIFEST_MAX_DESCRIPTOR_JSON_BYTES = 32 * 1024

_MIN_FENCE_LEN = 3
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")


@dataclass(frozen=True, slots=True)
class _IndexedSection:
    section_id: str
    heading: str
    level: int
    parent_id: str | None
    char_count: int
    path_key: str
    ordinal: int
    line: int
    start: int
    end: int


def _is_xml_nav_section(sec: Any) -> bool:
    return bool(sec.path.startswith("<"))


def _assign_section_ids(sections: list[Any]) -> list[_IndexedSection]:
    """Build stable ids with ``path#ordinal`` for duplicate paths."""
    body = [s for s in sections if s.level > 0]
    totals: dict[str, int] = {}
    for sec in body:
        totals[sec.path] = totals.get(sec.path, 0) + 1
    seen: dict[str, int] = {}
    indexed: list[_IndexedSection] = []
    stack: list[tuple[int, str]] = []
    for sec in body:
        path_key = sec.path
        seen[path_key] = seen.get(path_key, 0) + 1
        ord_n = seen[path_key]
        sid = path_key if totals[path_key] == 1 else f"{path_key}#{ord_n}"
        while stack and stack[-1][0] >= sec.level:
            stack.pop()
        parent_id = stack[-1][1] if stack else None
        indexed.append(
            _IndexedSection(
                section_id=sid,
                heading=sec.heading,
                level=sec.level,
                parent_id=parent_id,
                char_count=sec.chars,
                path_key=path_key,
                ordinal=ord_n,
                line=sec.line,
                start=sec.start,
                end=sec.end,
            )
        )
        stack.append((sec.level, sid))
    return indexed


def _preamble_span_len(text: str) -> int:
    sections = parse_sections(text)
    for sec in sections:
        if sec.level == 0:
            return sec.chars
    return 0


def _preamble_line(text: str) -> int:
    for sec in parse_sections(text):
        if sec.level == 0:
            return sec.line
    return 1


def _direct_child_ids(
    indexed: list[_IndexedSection], parent_section_id: str
) -> list[str]:
    return [r.section_id for r in indexed if r.parent_id == parent_section_id]


def _resolve_section_object(text: str, row: _IndexedSection) -> Any | None:
    sections = parse_sections(text)
    dupes = [s for s in sections if s.level > 0 and s.path == row.path_key]
    if not dupes:
        return None
    if len(dupes) > 1:
        dupes.sort(key=lambda s: s.line)
        if row.ordinal <= 0 or row.ordinal > len(dupes):
            return None
        return dupes[row.ordinal - 1]
    return dupes[0]


def _own_body_slice(text: str, sec: Any) -> str:
    """Section body before the first child heading (parent excludes nested)."""
    lines = text.splitlines(keepends=True)
    char_offset = 0
    in_range = False
    in_fence = False
    fence_char = ""
    fence_len = 0
    child_start: int | None = None
    for line in lines:
        if not in_range:
            if char_offset >= sec.start:
                in_range = True
            else:
                char_offset += len(line)
                continue
        stripped_fence = line.strip()
        if not in_fence:
            parsed = parse_fence_open(stripped_fence)
            if parsed is not None:
                ch, ln, _lang = parsed
                if ln >= _MIN_FENCE_LEN:
                    in_fence = True
                    fence_char, fence_len = ch, ln
                    char_offset += len(line)
                    continue
        elif is_closing_fence_line(stripped_fence, fence_char, fence_len):
            in_fence = False
            fence_char, fence_len = "", 0
            char_offset += len(line)
            continue
        if in_fence:
            char_offset += len(line)
            continue
        stripped = line.rstrip("\n\r")
        m = _HEADING_RE.match(stripped)
        if m and char_offset > sec.start:
            level = len(m.group(1))
            if level > sec.level:
                child_start = char_offset
                break
        char_offset += len(line)
    end = child_start if child_start is not None else sec.end
    return text[sec.start : end]


def _section_own_body(text: str, row: _IndexedSection) -> str | None:
    sec = _resolve_section_object(text, row)
    if sec is None:
        return None
    all_body = [s for s in parse_sections(text) if s.level > 0]
    children = [
        s
        for s in all_body
        if s.level > sec.level and s.start > sec.start and s.start < sec.end
    ]
    if children:
        return _own_body_slice(text, sec)
    return text[sec.start : sec.end]


def _subtree_char_count(text: str, row: _IndexedSection) -> int:
    sec = _resolve_section_object(text, row)
    if sec is None:
        return 0
    return sec.end - sec.start


def _page_descriptors(
    descriptors: list[dict[str, Any]],
    *,
    start: int = 0,
) -> tuple[list[dict[str, Any]], list[str], int | None, bool]:
    omitted: list[str] = []
    cursor: int | None = None
    truncated = False
    kept: list[dict[str, Any]] = []
    start = max(0, min(start, len(descriptors)))
    for idx in range(start, len(descriptors)):
        row = descriptors[idx]
        trial = kept + [row]
        if len(trial) > MANIFEST_MAX_DESCRIPTORS:
            truncated = True
            cursor = idx
            omitted.append("sections_tail")
            break
        encoded = json.dumps(trial, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MANIFEST_MAX_DESCRIPTOR_JSON_BYTES and kept:
            truncated = True
            cursor = idx
            omitted.append("sections_tail")
            break
        kept = trial
    if start > 0 and start < len(descriptors) and not truncated:
        truncated = start + len(kept) < len(descriptors)
        if truncated and cursor is None:
            cursor = start + len(kept)
            omitted.append("sections_tail")
    return kept, omitted, cursor, truncated


def _indexed_by_line(indexed: list[_IndexedSection]) -> dict[int, _IndexedSection]:
    return {row.line: row for row in indexed}


def _manifest_descriptors(text: str) -> list[dict[str, Any]]:
    """Navigation rows in document order (ATX + line-anchored XML blocks)."""
    nav = [s for s in _document_sections(text) if not (s.level == 0 and s.chars == 0)]
    preamble_len = _preamble_span_len(text)
    descriptors: list[dict[str, Any]] = []
    if preamble_len > 0:
        descriptors.append(
            {
                "id": PREAMBLE_ID,
                "heading": "[Preamble]",
                "level": 0,
                "parent_id": None,
                "line": _preamble_line(text),
                "char_count": preamble_len,
                "children": [],
                "subtree_char_count": preamble_len,
            }
        )
    atx_nav = [s for s in nav if s.level > 0 and not _is_xml_nav_section(s)]
    indexed = _assign_section_ids(atx_nav)
    by_line = _indexed_by_line(indexed)
    for sec in nav:
        if sec.level == 0:
            continue
        if _is_xml_nav_section(sec):
            descriptors.append(
                {
                    "id": sec.path,
                    "heading": sec.heading,
                    "level": sec.level,
                    "parent_id": None,
                    "line": sec.line,
                    "char_count": sec.chars,
                    "children": [],
                    "subtree_char_count": sec.chars,
                }
            )
            continue
        row = by_line.get(sec.line)
        if row is None:
            continue
        own = _section_own_body(text, row)
        own_len = len(own) if own is not None else row.char_count
        descriptors.append(
            {
                "id": row.section_id,
                "heading": row.heading,
                "level": row.level,
                "parent_id": row.parent_id,
                "line": row.line,
                "char_count": own_len,
                "children": _direct_child_ids(indexed, row.section_id),
                "subtree_char_count": _subtree_char_count(text, row),
            }
        )
    return descriptors


def build_document_manifest(
    *,
    path: str,
    sandbox: str,
    text: str,
    read_sha256: str,
    expected_read_sha256: str | None = None,
    descriptor_offset: int = 0,
) -> dict[str, Any]:
    if expected_read_sha256 and not sha256_hex_equal(read_sha256, expected_read_sha256):
        return _stale_read_root(
            read_sha256=read_sha256,
            expected_read_sha256=expected_read_sha256,
        )
    has_body = any(s.level > 0 for s in _document_sections(text))
    if not has_body:
        descriptors = [
            {
                "id": UNSECTIONED_ID,
                "heading": "",
                "level": 0,
                "parent_id": None,
                "line": 1,
                "char_count": len(text),
                "children": [],
                "subtree_char_count": len(text),
            }
        ]
        structure = "unsectioned"
        paged, omitted, cursor, truncated = _page_descriptors(
            descriptors, start=descriptor_offset
        )
        return {
            "path": path,
            "sandbox": sandbox,
            "read_sha256": read_sha256,
            "total_chars": len(text),
            "selection": "manifest",
            "structure": structure,
            "preamble": False,
            "sections": paged,
            "omitted": ["content", *omitted],
            "cursor": cursor,
            "truncated": truncated,
        }

    preamble_len = _preamble_span_len(text)
    descriptors = _manifest_descriptors(text)
    paged, omitted, cursor, truncated = _page_descriptors(
        descriptors, start=descriptor_offset
    )
    out: dict[str, Any] = {
        "path": path,
        "sandbox": sandbox,
        "read_sha256": read_sha256,
        "total_chars": len(text),
        "selection": "manifest",
        "structure": "sectioned",
        "preamble": preamble_len > 0,
        "sections": paged,
        "omitted": ["content", *omitted],
        "cursor": cursor,
        "truncated": truncated,
    }
    if truncated and cursor is not None:
        out["_next"] = f'fs(op="md_list", path="{path}", offset={cursor})'
    return out
