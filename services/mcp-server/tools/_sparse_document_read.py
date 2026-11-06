"""Manifest-first document reads and batched section expansion (slice 1)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from markdown_fence import is_closing_fence_line, parse_fence_open
from markdown_sections import SectionError, parse_sections, read_section

from tools._hashing import sha256_hex_of_bytes

_MIN_FENCE_LEN = 3

PREAMBLE_ID = "__preamble__"
UNSECTIONED_ID = "__unsectioned__"
MANIFEST_MAX_DESCRIPTORS = 200
MANIFEST_MAX_DESCRIPTOR_JSON_BYTES = 16 * 1024
BATCH_MAX_SECTIONS = 16
BATCH_MAX_CONTENT_CHARS = 64 * 1024

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


def sparse_selector_conflict_error(message: str) -> dict[str, Any]:
    return {
        "code": "invalid_selector_combo",
        "message": message,
        "source": "fs",
        "retryable": False,
        "data": {},
    }


def is_sparse_document_suffix(suffix: str) -> bool:
    lowered = suffix.lower()
    return lowered in {
        ".md",
        ".markdown",
        ".pdf",
        ".docx",
        ".odt",
        ".eml",
        ".html",
        ".htm",
    }


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
    return text[sec.start:end]


def _resolve_section_object(
    text: str, row: _IndexedSection
) -> Any | None:
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


def _resolve_indexed(
    indexed: list[_IndexedSection], section_id: str
) -> tuple[_IndexedSection | None, str | None]:
    if section_id == PREAMBLE_ID:
        return None, PREAMBLE_ID
    if section_id == UNSECTIONED_ID:
        for row in indexed:
            if row.section_id == UNSECTIONED_ID:
                return row, None
        return None, "missing"
    exact = [r for r in indexed if r.section_id == section_id]
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        return None, "duplicate_ambiguous"
    bare = [r for r in indexed if r.path_key == section_id]
    if len(bare) > 1:
        return None, "duplicate_ambiguous"
    if len(bare) == 1:
        return bare[0], None
    if "#" in section_id:
        base, _, ord_str = section_id.rpartition("#")
        if ord_str.isdigit():
            matches = [r for r in indexed if r.path_key == base and r.ordinal == int(ord_str)]
            if len(matches) == 1:
                return matches[0], None
    return None, "missing"


def _content_for_id(text: str, indexed: list[_IndexedSection], section_id: str) -> tuple[str | None, str]:
    if section_id == UNSECTIONED_ID:
        if not [s for s in parse_sections(text) if s.level > 0]:
            return text, "ok"
    if section_id == PREAMBLE_ID:
        sections = parse_sections(text)
        for sec in sections:
            if sec.level == 0:
                return text[sec.start : sec.end], "ok"
        return None, "missing"
    row, err = _resolve_indexed(indexed, section_id)
    if err == "duplicate_ambiguous":
        return None, "duplicate_ambiguous"
    if row is None:
        try:
            body = read_section(text, section_id)
            return body, "ok"
        except SectionError:
            return None, "missing"
    sections = parse_sections(text)
    by_path = {s.path: s for s in sections if s.level > 0}
    sec = by_path.get(row.path_key)
    if sec is None:
        return None, "missing"
    # Disambiguate duplicate paths by ordinal among same path_key
    dupes = [s for s in sections if s.level > 0 and s.path == row.path_key]
    if len(dupes) > 1:
        dupes.sort(key=lambda s: s.line)
        if row.ordinal <= 0 or row.ordinal > len(dupes):
            return None, "missing"
        sec = dupes[row.ordinal - 1]
    children = [
        s
        for s in sections
        if s.level > sec.level and s.start > sec.start and s.start < sec.end
    ]
    if children:
        return _own_body_slice(text, sec), "ok"
    return text[sec.start : sec.end], "ok"


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
) -> dict[str, Any]:
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
            }
        )
    for row in indexed:
        own = _section_own_body(text, row)
        descriptors.append(
            {
                "id": row.section_id,
                "heading": row.heading,
                "level": row.level,
                "parent_id": row.parent_id,
                "char_count": len(own) if own is not None else row.char_count,
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


def build_section_batch_response(
    *,
    path: str,
    sandbox: str,
    text: str,
    read_sha256: str,
    section_ids: list[str],
) -> dict[str, Any]:
    atx = [s for s in parse_sections(text) if s.level > 0]
    indexed = _assign_section_ids(atx) if atx else []
    items: list[dict[str, Any]] = []
    omitted: list[str] = []
    cursor: int | None = None
    content_budget = 0
    over_budget_from: int | None = None
    for idx, sid in enumerate(section_ids):
        if over_budget_from is not None:
            items.append({"id": sid, "outcome": "over_budget"})
            omitted.append(sid)
            continue
        if idx >= BATCH_MAX_SECTIONS:
            over_budget_from = idx
            cursor = idx
            items.append({"id": sid, "outcome": "over_budget"})
            omitted.append(sid)
            continue
        body, outcome = _content_for_id(text, indexed, sid)
        if outcome != "ok" or body is None:
            if outcome == "duplicate_ambiguous":
                items.append({"id": sid, "outcome": "duplicate_ambiguous"})
            else:
                items.append({"id": sid, "outcome": "missing"})
            continue
        next_chars = content_budget + len(body)
        if next_chars > BATCH_MAX_CONTENT_CHARS:
            over_budget_from = idx
            cursor = idx
            items.append({"id": sid, "outcome": "over_budget"})
            omitted.append(sid)
            continue
        content_budget = next_chars
        items.append(
            {
                "id": sid,
                "outcome": "ok",
                "content": body,
                "content_sha256": sha256_hex_of_bytes(body.encode("utf-8")),
            }
        )
    payload: dict[str, Any] = {
        "path": path,
        "sandbox": sandbox,
        "read_sha256": read_sha256,
        "items": items,
        "sparse_section_batch": True,
        "selection": "sections",
    }
    if omitted:
        payload["omitted"] = omitted
    if cursor is not None:
        payload["cursor"] = cursor
    return payload


def build_export_response(
    *,
    path: str,
    sandbox: str,
    text: str,
    read_sha256: str,
) -> dict[str, Any]:
    return {
        "path": path,
        "sandbox": sandbox,
        "content": text,
        "selection": "export",
        "read_sha256": read_sha256,
    }


def resolve_document_read(
    *,
    path: str,
    sandbox: str,
    text: str,
    read_sha256: str,
    section: str | None = None,
    sections: list[str] | None = None,
    export: bool = False,
    offset: int = 0,
    limit: int = 0,
) -> dict[str, Any]:
    has_section = bool(section and str(section).strip())
    has_sections = bool(sections)
    range_requested = offset > 0 or limit > 0

    if export and (has_section or has_sections or range_requested):
        return sparse_selector_conflict_error(
            "export=true is valid only alone without section, sections, or offset/limit"
        )
    if has_section and has_sections:
        return sparse_selector_conflict_error(
            "Pass either section or sections, not both"
        )
    if sections is not None and len(sections) == 0:
        return sparse_selector_conflict_error("sections must be a non-empty list when provided")

    if export:
        out = build_export_response(
            path=path, sandbox=sandbox, text=text, read_sha256=read_sha256
        )
        out["sparse_explicit_export"] = True
        return out
    if sections:
        return build_section_batch_response(
            path=path,
            sandbox=sandbox,
            text=text,
            read_sha256=read_sha256,
            section_ids=list(sections),
        )
    if has_section:
        atx = [s for s in parse_sections(text) if s.level > 0]
        indexed = _assign_section_ids(atx) if atx else []
        body, outcome = _content_for_id(text, indexed, str(section).strip())
        if outcome == "duplicate_ambiguous":
            candidates = [r.section_id for r in indexed if r.path_key == section]
            return {
                "code": "duplicate_ambiguous",
                "message": f"Section id {section!r} is ambiguous",
                "source": "fs",
                "retryable": False,
                "data": {"candidates": candidates},
                "items": [{"id": section, "outcome": "duplicate_ambiguous"}],
            }
        if outcome != "ok" or body is None:
            return {"error": f"Section not found: {section!r}"}
        return {
            "content": body,
            "path": path,
            "sandbox": sandbox,
            "section": section,
            "read_sha256": read_sha256,
            "content_sha256": sha256_hex_of_bytes(body.encode("utf-8")),
        }
    if range_requested:
        from tools._line_range import apply_line_range

        window, meta = apply_line_range(text, offset, limit)
        out: dict[str, Any] = {
            "content": window,
            "path": path,
            "sandbox": sandbox,
            "selection": "window",
            "read_sha256": read_sha256,
        }
        out.update(meta)
        return out
    return build_document_manifest(
        path=path, sandbox=sandbox, text=text, read_sha256=read_sha256
    )


def read_sha256_for_bytes(data: bytes) -> str:
    return sha256_hex_of_bytes(data)
