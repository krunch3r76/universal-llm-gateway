"""Opt-in lean section listing for md_list — one parse, depth-filtered rows."""

from __future__ import annotations

import json
from typing import Any

from markdown_sections import PREAMBLE_ID, _document_sections

from tools._document_manifest import (
    UNSECTIONED_ID,
    _assign_section_ids,
    _direct_child_ids,
    _indexed_by_line,
    _is_xml_nav_section,
    _own_body_slice,
)
from tools._hashing import sha256_hex_equal
from tools._sparse_payload_envelope import _stale_read_root

# Payload budget ~3.5 KiB so Event Service response_bytes stay under ~8 KiB wire.
LEAN_MAX_DESCRIPTOR_JSON_BYTES = 3584


def _preamble_from_nav(nav: list[Any]) -> tuple[int, int]:
    for sec in nav:
        if sec.level == 0:
            return int(sec.chars), int(sec.line)
    return 0, 1


def _lean_atx_row(text: str, row: Any, indexed: list[Any], sec: Any) -> dict[str, Any]:
    child_ids = _direct_child_ids(indexed, row.section_id)
    if child_ids:
        own_len = len(_own_body_slice(text, sec))
        return {
            "id": row.section_id,
            "level": row.level,
            "char_count": own_len,
            "subtree_char_count": sec.end - sec.start,
            "child_count": len(child_ids),
        }
    return {
        "id": row.section_id,
        "level": row.level,
        "char_count": len(text[sec.start : sec.end]),
    }


def _build_lean_rows(text: str, nav: list[Any]) -> list[dict[str, Any]]:
    trimmed = [s for s in nav if not (s.level == 0 and s.chars == 0)]
    rows: list[dict[str, Any]] = []
    preamble_len, _line = _preamble_from_nav(nav)
    if preamble_len > 0:
        rows.append(
            {
                "id": PREAMBLE_ID,
                "level": 0,
                "char_count": preamble_len,
                "subtree_char_count": preamble_len,
            }
        )
    atx_nav = [s for s in trimmed if s.level > 0 and not _is_xml_nav_section(s)]
    indexed = _assign_section_ids(atx_nav)
    by_line = _indexed_by_line(indexed)
    for sec in trimmed:
        if sec.level == 0:
            continue
        if _is_xml_nav_section(sec):
            rows.append(
                {
                    "id": sec.path,
                    "level": sec.level,
                    "char_count": sec.chars,
                    "subtree_char_count": sec.chars,
                }
            )
            continue
        row = by_line.get(sec.line)
        if row is None:
            continue
        rows.append(_lean_atx_row(text, row, indexed, sec))
    return rows


def _filter_by_depth(rows: list[dict[str, Any]], max_depth: int) -> tuple[list[dict[str, Any]], int]:
    kept = [r for r in rows if int(r["level"]) <= max_depth]
    return kept, len(rows) - len(kept)


def _page_lean_rows(
    rows: list[dict[str, Any]],
    *,
    start: int = 0,
) -> tuple[list[dict[str, Any]], list[str], int | None, bool]:
    omitted: list[str] = []
    cursor: int | None = None
    truncated = False
    kept: list[dict[str, Any]] = []
    start = max(0, min(start, len(rows)))
    for idx in range(start, len(rows)):
        row = rows[idx]
        trial = kept + [row]
        encoded = json.dumps(trial, ensure_ascii=False).encode("utf-8")
        if len(encoded) > LEAN_MAX_DESCRIPTOR_JSON_BYTES and kept:
            truncated = True
            cursor = idx
            omitted.append("sections_tail")
            break
        kept = trial
    if start > 0 and start < len(rows) and not truncated:
        truncated = start + len(kept) < len(rows)
        if truncated and cursor is None:
            cursor = start + len(kept)
            omitted.append("sections_tail")
    return kept, omitted, cursor, truncated


def build_lean_document_manifest(
    *,
    path: str,
    sandbox: str,
    text: str,
    read_sha256: str,
    max_depth: int,
    descriptor_offset: int = 0,
    cursor_scope_depth: int = 0,
    expected_read_sha256: str | None = None,
) -> dict[str, Any]:
    if expected_read_sha256 and not sha256_hex_equal(read_sha256, expected_read_sha256):
        return _stale_read_root(
            read_sha256=read_sha256,
            expected_read_sha256=expected_read_sha256,
        )
    depth = max(1, min(6, int(max_depth)))
    nav = _document_sections(text)
    has_body = any(s.level > 0 for s in nav)
    if not has_body:
        rows = [
            {
                "id": UNSECTIONED_ID,
                "level": 0,
                "char_count": len(text),
                "subtree_char_count": len(text),
            }
        ]
        filtered = rows
        depth_cut = 0
    else:
        all_rows = _build_lean_rows(text, nav)
        filtered, depth_cut = _filter_by_depth(all_rows, depth)

    start = max(0, int(descriptor_offset))
    if start > 0 and cursor_scope_depth > 0 and cursor_scope_depth != depth:
        return {
            "code": "invalid_cursor_scope",
            "message": (
                f"Lean listing offset={start} was issued at max_depth={cursor_scope_depth}; "
                f"got max_depth={depth}. Pass the same max_depth or restart from offset=0."
            ),
            "source": "fs",
            "retryable": False,
            "data": {
                "lean": True,
                "cursor_scope_depth": cursor_scope_depth,
                "max_depth_applied": depth,
            },
        }
    if start > len(filtered):
        return {
            "code": "invalid_cursor_scope",
            "message": (
                f"Lean listing cursor offset={start} is out of range for "
                f"lean=true, max_depth={depth} ({len(filtered)} rows). "
                "Re-list from offset=0 with the same lean and max_depth."
            ),
            "source": "fs",
            "retryable": False,
            "data": {"lean": True, "max_depth_applied": depth},
        }

    paged, omitted, cursor, truncated = _page_lean_rows(filtered, start=start)
    out: dict[str, Any] = {
        "path": path,
        "sandbox": sandbox,
        "read_sha256": read_sha256,
        "total_chars": len(text),
        "selection": "lean",
        "lean": True,
        "max_depth_applied": depth,
        "depth_rows_omitted": depth_cut,
        "rows": paged,
        "omitted": list(omitted),
        "cursor": cursor,
        "truncated": truncated,
        "cursor_scope_depth": depth,
    }
    if truncated and cursor is not None:
        out["_next"] = (
            f'fs(op="md_list", path="{path}", offset={cursor}, '
            f"lean=true, max_depth={depth})"
        )
    return out


def lean_listing_scope_error(*, offset: int, lean: bool, max_depth: int) -> dict[str, Any]:
    return {
        "code": "invalid_cursor_scope",
        "message": (
            f"md_list offset={offset} requires lean=true with a stable max_depth; "
            f"got lean={lean}, max_depth={max_depth}. Re-list from offset=0."
        ),
        "source": "fs",
        "retryable": False,
        "data": {},
    }