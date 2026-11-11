"""Manifest-first document reads and batched section expansion (slice 1)."""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from typing import Any

from markdown_fence import is_closing_fence_line, parse_fence_open
from markdown_sections import SectionError, parse_sections, resolve_section

from tools._hashing import sha256_hex_equal, sha256_hex_of_bytes

_MIN_FENCE_LEN = 3

PREAMBLE_ID = "__preamble__"
UNSECTIONED_ID = "__unsectioned__"
MANIFEST_MAX_DESCRIPTORS = 200
MANIFEST_MAX_DESCRIPTOR_JSON_BYTES = 16 * 1024
BATCH_MAX_SECTIONS = 16
BATCH_MAX_CONTENT_CHARS = 64 * 1024
# 65536 code points × 4 UTF-8 bytes max (BMP scalar) — defensive wire ceiling.
SPARSE_MAX_CONTENT_BYTES = 262_144
SPARSE_POLICY_SLICE1 = "slice1_document_and_entity_preview"

_ID_FORMAT_HINT = (
    "Section ids are heading paths 'Parent/Child'; duplicate headings get '#n'. "
    "Call md_list to enumerate ids."
)

_WINDOW_GATE_NEXT = (
    "Markdown/document windows need expected_read_sha256=<read_sha256 from this "
    "manifest> (proof you listed first). Or read named sections (md_read "
    "section=/sections=), or pass export=true (sanctioned whole-document override, "
    "chunked via chunk_cursor)."
)

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


def param_not_applicable_error(
    *,
    parameter: str,
    keys: list[str] | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {"parameter": parameter}
    if keys:
        data["keys"] = list(keys)
    return {
        "code": "param_not_applicable",
        "message": message
        or f"Parameter {parameter!r} does not apply to this read target or op combination.",
        "source": "fs",
        "retryable": False,
        "data": data,
    }


def _sum_utf8_content_bytes(node: Any) -> int:
    total = 0
    if isinstance(node, dict):
        for key, val in node.items():
            if key == "content" and isinstance(val, str):
                total += len(val.encode("utf-8"))
            else:
                total += _sum_utf8_content_bytes(val)
    elif isinstance(node, list):
        for item in node:
            total += _sum_utf8_content_bytes(item)
    return total


def finalize_sparse_document_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Attach sparse policy marker and enforce the UTF-8 byte ceiling (slice 1)."""
    if payload.get("code") in {"param_not_applicable", "invalid_selector_combo"}:
        return payload
    selection = payload.get("selection")
    if selection == "multi":
        byte_sum = _sum_utf8_content_bytes(payload)
        if byte_sum <= SPARSE_MAX_CONTENT_BYTES:
            payload["sparse_policy"] = SPARSE_POLICY_SLICE1
        return payload
    if selection not in {"manifest", "section", "sections", "export", "window"}:
        return payload
    byte_sum = _sum_utf8_content_bytes(payload)
    if byte_sum > SPARSE_MAX_CONTENT_BYTES:
        return {
            "code": "sparse_payload_over_byte_ceiling",
            "message": (
                f"Sparse document response exceeded UTF-8 content ceiling "
                f"({byte_sum} bytes > {SPARSE_MAX_CONTENT_BYTES})"
            ),
            "source": "fs",
            "retryable": False,
            "data": {"measured_utf8_bytes": byte_sum},
        }
    payload["sparse_policy"] = SPARSE_POLICY_SLICE1
    return payload


def _stale_read_root(*, read_sha256: str, expected_read_sha256: str) -> dict[str, Any]:
    return finalize_sparse_document_payload(
        {
            "ok": False,
            "outcome": "stale_handle",
            "read_sha256": read_sha256,
            "expected_read_sha256": expected_read_sha256,
            "selection": "manifest",
            "omitted": ["content"],
        }
    )


def _direct_child_ids(indexed: list[_IndexedSection], parent_path: str) -> list[str]:
    return [r.section_id for r in indexed if r.parent_id == parent_path]


def _subtree_char_count(text: str, row: _IndexedSection) -> int:
    sec = _resolve_section_object(text, row)
    if sec is None:
        return 0
    return sec.end - sec.start


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


def _id_last_segment(section_id: str) -> str:
    """Last heading label in a section id, without ``#ordinal`` or parent path."""
    base = section_id.rsplit("#", 1)[0]
    return base.rsplit("/", 1)[-1]


def _nearest_section_ids(sid: str, indexed: list[_IndexedSection], *, limit: int = 5) -> list[str]:
    """Rank document section ids to help callers recover from a miss.

    Order: case-insensitive exact id, ``difflib`` close matches (cutoff 0.5),
    then ids whose last path segment matches the request. At most ``limit`` ids,
    all from the document index.
    """
    doc_ids = [r.section_id for r in indexed]
    if not doc_ids:
        return []
    ranked: list[str] = []
    seen: set[str] = set()

    def _append(candidates: list[str]) -> None:
        for cid in candidates:
            if cid in seen or cid not in doc_ids:
                continue
            seen.add(cid)
            ranked.append(cid)
            if len(ranked) >= limit:
                return

    lower = sid.lower()
    _append([i for i in doc_ids if i.lower() == lower])
    if len(ranked) < limit:
        _append(difflib.get_close_matches(sid, doc_ids, n=limit, cutoff=0.5))
    if len(ranked) < limit:
        req_tail = _id_last_segment(sid)
        _append([i for i in doc_ids if _id_last_segment(i) == req_tail])
    return ranked[:limit]


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
    return text[sec.start : end]


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
            matches = [
                r for r in indexed if r.path_key == base and r.ordinal == int(ord_str)
            ]
            if len(matches) == 1:
                return matches[0], None
    return None, "missing"


def _honest_span_meta(
    *,
    body: str,
    children: list[str],
    subtree: int,
) -> dict[str, Any]:
    """Own-body disclosure. ``char_count`` is the returned slice, never a zero stand-in."""
    return {
        "span": "own_body",
        "includes_children": False,
        "children": children,
        "char_count": len(body),
        "subtree_char_count": subtree,
    }


def _empty_span_meta() -> dict[str, Any]:
    return _honest_span_meta(body="", children=[], subtree=0)


def _meta_for_resolved_section(text: str, sec: Any) -> tuple[str, dict[str, Any]]:
    """Own-body slice plus real child ids for a resolved ATX or XML section.

    Leaf-name, suffix, and XML fallbacks used to return the child-inclusive
    ``read_section`` span while labelling it own-body with empty children.
    """
    atx = [s for s in parse_sections(text) if s.level > 0]
    nested = [
        s
        for s in atx
        if s.level > sec.level and s.start > sec.start and s.start < sec.end
    ]
    direct = [s for s in nested if s.level == sec.level + 1]
    if nested and not direct:
        min_level = min(s.level for s in nested)
        direct = [s for s in nested if s.level == min_level]
    body = _own_body_slice(text, sec) if nested else text[sec.start : sec.end]
    subtree = sec.end - sec.start
    return body, _honest_span_meta(
        body=body,
        children=[s.path for s in direct],
        subtree=subtree,
    )


def _content_for_id(
    text: str, indexed: list[_IndexedSection], section_id: str
) -> tuple[str | None, str]:
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
            sec = resolve_section(text, section_id)
        except SectionError:
            return None, "missing"
        body, _meta = _meta_for_resolved_section(text, sec)
        return body, "ok"
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


def _span_meta_for_section_id(
    text: str, indexed: list[_IndexedSection], section_id: str
) -> dict[str, Any]:
    if section_id == PREAMBLE_ID:
        for sec in parse_sections(text):
            if sec.level == 0:
                body = text[sec.start : sec.end]
                return _honest_span_meta(body=body, children=[], subtree=len(body))
        return _empty_span_meta()
    if section_id == UNSECTIONED_ID:
        if not [s for s in parse_sections(text) if s.level > 0]:
            return _honest_span_meta(body=text, children=[], subtree=len(text))
        return _empty_span_meta()
    row, err = _resolve_indexed(indexed, section_id)
    if err is not None:
        return _empty_span_meta()
    if row is None:
        try:
            sec = resolve_section(text, section_id)
        except SectionError:
            return _empty_span_meta()
        _body, meta = _meta_for_resolved_section(text, sec)
        return meta
    own = _section_own_body(text, row)
    own_len = len(own) if own is not None else row.char_count
    child_ids = _direct_child_ids(indexed, row.path_key)
    subtree = _subtree_char_count(text, row)
    return {
        "span": "own_body",
        "includes_children": False,
        "children": child_ids,
        "char_count": own_len,
        "subtree_char_count": subtree,
    }


def _stopped_section_item(
    sid: str,
    *,
    span_meta: dict[str, Any],
    body: str | None,
    outcome: str,
) -> dict[str, Any]:
    """An undelivered sibling after the batch has stopped.

    A sibling whose own body exceeds the cap still carries ``oversize`` and
    ``fetch``. A sibling that is over budget only because an earlier item
    stopped the batch does not.
    """
    item: dict[str, Any] = {"id": sid, "outcome": "over_budget", **span_meta}
    if (
        outcome == "ok"
        and isinstance(body, str)
        and len(body) > BATCH_MAX_CONTENT_CHARS
    ):
        item["oversize"] = True
        item["char_count"] = len(body)
        item["fetch"] = {"section": sid, "chunk_cursor": 0}
    return item


def build_section_batch_response(
    *,
    path: str,
    sandbox: str,
    text: str,
    read_sha256: str,
    section_ids: list[str],
    expected_read_sha256: str | None = None,
) -> dict[str, Any]:
    if expected_read_sha256 and not sha256_hex_equal(read_sha256, expected_read_sha256):
        return {
            "ok": False,
            "outcome": "stale_handle",
            "read_sha256": read_sha256,
            "expected_read_sha256": expected_read_sha256,
            "items": [{"id": sid, "outcome": "stale_handle"} for sid in section_ids],
            "selection": "sections",
            "sparse_section_batch": True,
        }
    atx = [s for s in parse_sections(text) if s.level > 0]
    indexed = _assign_section_ids(atx) if atx else []
    items: list[dict[str, Any]] = []
    omitted: list[str] = []
    budget_cursor: int | None = None
    content_budget = 0
    over_budget_from: int | None = None
    for idx, sid in enumerate(section_ids):
        span_meta = _span_meta_for_section_id(text, indexed, sid)
        if over_budget_from is not None or idx >= BATCH_MAX_SECTIONS:
            if over_budget_from is None:
                over_budget_from = idx
                budget_cursor = idx
            body, outcome = _content_for_id(text, indexed, sid)
            items.append(
                _stopped_section_item(
                    sid, span_meta=span_meta, body=body, outcome=outcome
                )
            )
            omitted.append(sid)
            continue
        body, outcome = _content_for_id(text, indexed, sid)
        if outcome != "ok" or body is None:
            if outcome == "duplicate_ambiguous":
                items.append({"id": sid, "outcome": "duplicate_ambiguous", **span_meta})
            else:
                items.append({"id": sid, "outcome": "missing", **span_meta})
            continue
        body_len = len(body)
        if body_len > BATCH_MAX_CONTENT_CHARS:
            over_budget_from = idx
            budget_cursor = idx
            items.append(
                {
                    "id": sid,
                    "outcome": "over_budget",
                    "oversize": True,
                    "char_count": body_len,
                    "fetch": {"section": sid, "chunk_cursor": 0},
                    **span_meta,
                }
            )
            omitted.append(sid)
            continue
        next_chars = content_budget + body_len
        if next_chars > BATCH_MAX_CONTENT_CHARS:
            over_budget_from = idx
            budget_cursor = idx
            items.append({"id": sid, "outcome": "over_budget", **span_meta})
            omitted.append(sid)
            continue
        content_budget = next_chars
        items.append(
            {
                "id": sid,
                "outcome": "ok",
                "content": body,
                "content_sha256": sha256_hex_of_bytes(body.encode("utf-8")),
                **span_meta,
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
    if budget_cursor is not None:
        payload["budget_cursor"] = budget_cursor
    return payload


def _chunk_cursor_out_of_range(section_label: str | None = None) -> dict[str, Any]:
    return {
        "code": "chunk_cursor_out_of_range",
        "message": "chunk_cursor is out of range for the selected body",
        "source": "fs",
        "retryable": False,
        "data": ({} if section_label is None else {"section": section_label}),
    }


def build_export_response(
    *,
    path: str,
    sandbox: str,
    text: str,
    read_sha256: str,
    chunk_cursor: int | None = None,
    expected_read_sha256: str | None = None,
) -> dict[str, Any]:
    if expected_read_sha256 and not sha256_hex_equal(read_sha256, expected_read_sha256):
        return {
            "ok": False,
            "outcome": "stale_handle",
            "read_sha256": read_sha256,
            "expected_read_sha256": expected_read_sha256,
            "selection": "export",
        }
    start = 0 if chunk_cursor is None else int(chunk_cursor)
    if start < 0 or start > len(text):
        return _chunk_cursor_out_of_range()
    chunk = text[start : start + BATCH_MAX_CONTENT_CHARS]
    next_pos = start + len(chunk)
    chunk_next = next_pos if next_pos < len(text) else None
    truncated = chunk_next is not None
    return {
        "path": path,
        "sandbox": sandbox,
        "content": chunk,
        "selection": "export",
        "read_sha256": read_sha256,
        "char_count": len(text),
        "content_sha256": sha256_hex_of_bytes(chunk.encode("utf-8")),
        "chunk_cursor": start,
        "chunk_cursor_next": chunk_next,
        "truncated": truncated,
        "sparse_explicit_export": True,
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
    chunk_cursor: int | None = None,
    chunk_cursor_provided: bool = False,
    expected_read_sha256: str | None = None,
) -> dict[str, Any]:
    """Manifest-first markdown/document read dispatcher (single section and batch).

    When a single ``section`` id is missing, the payload includes ``nearest_ids``
    (up to five ranked ids from the document) and ``id_format_hint`` (how ids
    are formed). When the id is ``duplicate_ambiguous``, the early return carries
    the colliding ids in ``nearest_ids`` plus the same hint.
    """
    has_section = bool(section and str(section).strip())
    has_sections = bool(sections)
    range_requested = offset > 0 or limit > 0

    if chunk_cursor_provided and has_sections:
        return sparse_selector_conflict_error(
            "chunk_cursor is not valid together with sections="
        )
    if chunk_cursor_provided and range_requested:
        return sparse_selector_conflict_error(
            "chunk_cursor is not valid together with offset/limit window reads"
        )
    if chunk_cursor_provided and not export and not (has_section and not has_sections):
        return param_not_applicable_error(parameter="chunk_cursor")

    if export and (has_section or has_sections or range_requested):
        return sparse_selector_conflict_error(
            "export=true is valid only alone without section, sections, or offset/limit"
        )
    if has_section and has_sections:
        return sparse_selector_conflict_error(
            "Pass either section or sections, not both"
        )
    if sections is not None and len(sections) == 0:
        return sparse_selector_conflict_error(
            "sections must be a non-empty list when provided"
        )

    if export:
        out = build_export_response(
            path=path,
            sandbox=sandbox,
            text=text,
            read_sha256=read_sha256,
            chunk_cursor=chunk_cursor,
            expected_read_sha256=expected_read_sha256,
        )
        if out.get("code") == "chunk_cursor_out_of_range":
            return out
        return finalize_sparse_document_payload(out)

    if sections:
        batch = build_section_batch_response(
            path=path,
            sandbox=sandbox,
            text=text,
            read_sha256=read_sha256,
            section_ids=list(sections),
            expected_read_sha256=expected_read_sha256,
        )
        return finalize_sparse_document_payload(batch)

    if has_section:
        sid = str(section).strip()
        atx = [s for s in parse_sections(text) if s.level > 0]
        indexed = _assign_section_ids(atx) if atx else []
        span_meta = _span_meta_for_section_id(text, indexed, sid)
        if expected_read_sha256 and not sha256_hex_equal(
            read_sha256, expected_read_sha256
        ):
            return finalize_sparse_document_payload(
                {
                    "ok": False,
                    "outcome": "stale_handle",
                    "read_sha256": read_sha256,
                    "expected_read_sha256": expected_read_sha256,
                    "selection": "section",
                    "section": sid,
                    **span_meta,
                }
            )
        body, outcome = _content_for_id(text, indexed, sid)
        if outcome == "duplicate_ambiguous":
            candidates = [r.section_id for r in indexed if r.path_key == sid]
            return finalize_sparse_document_payload(
                {
                    "code": "duplicate_ambiguous",
                    "message": f"Section id {section!r} is ambiguous",
                    "source": "fs",
                    "retryable": False,
                    "data": {"candidates": candidates},
                    "items": [{"id": sid, "outcome": "duplicate_ambiguous"}],
                    "selection": "section",
                    "nearest_ids": candidates,
                    "id_format_hint": _ID_FORMAT_HINT,
                }
            )
        if outcome != "ok" or body is None:
            return finalize_sparse_document_payload(
                {
                    "ok": False,
                    "outcome": "missing",
                    "error": f"Section not found: {sid!r}",
                    "selection": "section",
                    "section": sid,
                    "path": path,
                    "sandbox": sandbox,
                    "read_sha256": read_sha256,
                    "nearest_ids": _nearest_section_ids(sid, indexed),
                    "id_format_hint": _ID_FORMAT_HINT,
                    **span_meta,
                }
            )

        if chunk_cursor_provided:
            start = 0 if chunk_cursor is None else int(chunk_cursor)
            if start < 0 or start > len(body):
                return _chunk_cursor_out_of_range(section_label=sid)
            chunk = body[start : start + BATCH_MAX_CONTENT_CHARS]
            next_pos = start + len(chunk)
            chunk_next = next_pos if next_pos < len(body) else None
            truncated = chunk_next is not None
            single: dict[str, Any] = {
                "ok": True,
                "outcome": "ok",
                "content": chunk,
                "path": path,
                "sandbox": sandbox,
                "section": sid,
                "read_sha256": read_sha256,
                "content_sha256": sha256_hex_of_bytes(chunk.encode("utf-8")),
                "chunk_cursor": start,
                "chunk_cursor_next": chunk_next,
                "truncated": truncated,
                "char_count": len(body),
                "selection": "section",
                **span_meta,
            }
            return finalize_sparse_document_payload(single)

        if len(body) > BATCH_MAX_CONTENT_CHARS:
            return finalize_sparse_document_payload(
                {
                    "ok": False,
                    "outcome": "over_budget",
                    "oversize": True,
                    "char_count": len(body),
                    "fetch": {"section": sid, "chunk_cursor": 0},
                    "read_sha256": read_sha256,
                    "selection": "section",
                    "section": sid,
                    **span_meta,
                }
            )
        return finalize_sparse_document_payload(
            {
                "ok": True,
                "outcome": "ok",
                "content": body,
                "path": path,
                "sandbox": sandbox,
                "section": sid,
                "read_sha256": read_sha256,
                "content_sha256": sha256_hex_of_bytes(body.encode("utf-8")),
                "selection": "section",
                **span_meta,
            }
        )

    if range_requested:
        if not expected_read_sha256:
            manifest = build_document_manifest(
                path=path,
                sandbox=sandbox,
                text=text,
                read_sha256=read_sha256,
            )
            manifest["window_gated"] = True
            manifest["window_requested"] = {"offset": offset, "limit": limit}
            manifest["_next"] = _WINDOW_GATE_NEXT
            return finalize_sparse_document_payload(manifest)
        if not sha256_hex_equal(read_sha256, expected_read_sha256):
            return finalize_sparse_document_payload(
                {
                    "ok": False,
                    "outcome": "stale_handle",
                    "read_sha256": read_sha256,
                    "expected_read_sha256": expected_read_sha256,
                    "selection": "window",
                    "path": path,
                    "sandbox": sandbox,
                }
            )
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
        return finalize_sparse_document_payload(out)
    manifest = build_document_manifest(
        path=path,
        sandbox=sandbox,
        text=text,
        read_sha256=read_sha256,
        expected_read_sha256=expected_read_sha256,
    )
    return finalize_sparse_document_payload(manifest)


def read_sha256_for_bytes(data: bytes) -> str:
    return sha256_hex_of_bytes(data)
