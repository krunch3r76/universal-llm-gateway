"""Document manifest builders for sparse md/PDF reads (slice 1)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from markdown_fence import is_closing_fence_line, parse_fence_open
from markdown_sections import parse_sections

from tools._hashing import sha256_hex_equal
from tools._sparse_payload_envelope import _stale_read_root

PREAMBLE_ID = "__preamble__"
UNSECTIONED_ID = "__unsectioned__"
MANIFEST_MAX_DESCRIPTORS = 200
MANIFEST_MAX_DESCRIPTOR_JSON_BYTES = 16 * 1024

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
    start: int
    end: int


def _parent_id_for_path(path: str) -> str | None:
    if not path or "/" not in path:
        return None
    parent = path.rsplit("/", 1)[0]
    return parent


def _assign_section_ids(sections: list[Any]) -> list[_IndexedSection]:
    """Build stable ids with ``path#ordinal`` for duplicate paths."""
    body = [s for s in sections if s.level > 0]
    totals: dict[str, int] = {}
    for sec in body:
        totals[sec.path] = totals.get(sec.path, 0) + 1
    seen: dict[str, int] = {}
    indexed: list[_IndexedSection] = []
    for sec in body:
        path_key = sec.path
        seen[path_key] = seen.get(path_key, 0) + 1
        ord_n = seen[path_key]
        sid = path_key if totals[path_key] == 1 else f"{path_key}#{ord_n}"
        indexed.append(
            _IndexedSection(
                section_id=sid,
                heading=sec.heading,
                level=sec.level,
                parent_id=_parent_id_for_path(path_key),
                char_count=sec.chars,
                path_key=path_key,
                ordinal=ord_n,
                start=sec.start,
                end=sec.end,
            )
        )
    return indexed


def _preamble_span_len(text: str) -> int:
    sections = parse_sections(text)
    for sec in sections:
        if sec.level == 0:
            return sec.chars
    return 0


def _direct_child_ids(indexed: list[_IndexedSection], parent_path: str) -> list[str]:
    return [r.section_id for r in indexed if r.parent_id == parent_path]


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
) -> tuple[list[dict[str, Any]], list[str], int | None, bool]:
    omitted: list[str] = []
    cursor: int | None = None
    truncated = False
    kept: list[dict[str, Any]] = []
    for idx, row in enumerate(descriptors):
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
    return kept, omitted, cursor, truncated


def build_document_manifest(
    *,
    path: str,
    sandbox: str,
    text: str,
    read_sha256: str,
    expected_read_sha256: str | None = None,
) -> dict[str, Any]:
    if expected_read_sha256 and not sha256_hex_equal(read_sha256, expected_read_sha256):
        return _stale_read_root(
            read_sha256=read_sha256,
            expected_read_sha256=expected_read_sha256,
        )
    atx = [s for s in parse_sections(text) if s.level > 0]
    preamble_len = _preamble_span_len(text)
    if not atx:
        descriptors = [
            {
                "id": UNSECTIONED_ID,
                "heading": "",
                "level": 0,
                "parent_id": None,
                "char_count": len(text),
                "children": [],
                "subtree_char_count": len(text),
            }
        ]
        structure = "unsectioned"
        paged, omitted, cursor, truncated = _page_descriptors(descriptors)
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

    indexed = _assign_section_ids(atx)
    descriptors: list[dict[str, Any]] = []
    if preamble_len > 0:
        descriptors.append(
            {
                "id": PREAMBLE_ID,
                "heading": "[Preamble]",
                "level": 0,
                "parent_id": None,
                "char_count": preamble_len,
                "children": [],
                "subtree_char_count": preamble_len,
            }
        )
    for row in indexed:
        own = _section_own_body(text, row)
        own_len = len(own) if own is not None else row.char_count
        child_ids = _direct_child_ids(indexed, row.path_key)
        subtree = _subtree_char_count(text, row)
        descriptors.append(
            {
                "id": row.section_id,
                "heading": row.heading,
                "level": row.level,
                "parent_id": row.parent_id,
                "char_count": own_len,
                "children": child_ids,
                "subtree_char_count": subtree,
            }
        )
    paged, omitted, cursor, truncated = _page_descriptors(descriptors)
    return {
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
