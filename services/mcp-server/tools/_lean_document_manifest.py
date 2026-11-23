"""Opt-in lean section listing for md_list, plus non-lean offset containment.

Callers are the markdown tool and ``fs_impl``. Lean listings stay opt-in and
keep self-describing ``d{depth}:{row}`` cursors. Every other fs op receives
only an int offset so a digit string cannot reach line slicing as text.
"""

from __future__ import annotations

import json
from typing import Any

from markdown_sections import PREAMBLE_ID, _document_sections
from mcp_events import record

from tools._document_manifest import (
    UNSECTIONED_ID,
    _assign_section_ids,
    _direct_child_ids,
    _indexed_by_line,
    _is_xml_nav_section,
    _own_body_slice,
)
from tools._hashing import sha256_hex_equal
from tools._lean_offset_codec import (
    encode_lean_descriptor_offset,
    invalid_cursor_envelope,
    lean_offset_pattern,
    parse_lean_descriptor_offset,
)
from tools._sparse_payload_envelope import _stale_read_root

LEAN_MAX_DESCRIPTOR_JSON_BYTES = 3584


def _validate_max_depth(max_depth: int) -> tuple[int | None, dict[str, Any] | None]:
    depth = int(max_depth)
    if depth < 1 or depth > 6:
        return None, {
            "code": "invalid_max_depth",
            "message": f"lean max_depth must be between 1 and 6 inclusive; got {depth}.",
            "source": "fs",
            "retryable": False,
            "data": {"lean": True, "max_depth_requested": depth},
        }
    return depth, None


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


def _build_lean_rows(
    text: str, nav: list[Any], *, max_depth: int
) -> tuple[list[dict[str, Any]], int]:
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
    depth_omitted = 0
    for sec in trimmed:
        if sec.level == 0:
            continue
        if sec.level > max_depth:
            depth_omitted += 1
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
    return rows, depth_omitted


def _page_lean_rows(
    rows: list[dict[str, Any]],
    *,
    start: int = 0,
    depth: int,
) -> tuple[list[dict[str, Any]], list[str], str | None, bool]:
    omitted: list[str] = []
    cursor: str | None = None
    truncated = False
    kept: list[dict[str, Any]] = []
    start = max(0, min(start, len(rows)))
    for idx in range(start, len(rows)):
        trial = kept + [rows[idx]]
        if (
            len(json.dumps(trial, ensure_ascii=False).encode("utf-8"))
            > LEAN_MAX_DESCRIPTOR_JSON_BYTES
            and kept
        ):
            truncated = True
            cursor = encode_lean_descriptor_offset(depth, idx)
            omitted.append("sections_tail")
            break
        kept = trial
    if not truncated and start + len(kept) < len(rows):
        truncated = True
        cursor = encode_lean_descriptor_offset(depth, start + len(kept))
        omitted.append("sections_tail")
    return kept, omitted, cursor, truncated


def build_lean_document_manifest(
    *,
    path: str,
    sandbox: str,
    text: str,
    read_sha256: str,
    max_depth: int,
    descriptor_offset: int | str = 0,
    expected_read_sha256: str | None = None,
) -> dict[str, Any]:
    """Build one depth-filtered lean manifest from text already in memory.

    Parses sections once, drops rows deeper than max_depth, and pages from
    descriptor_offset (an int start or a d{depth}:{row} cursor). Returns the
    lean payload, or an error envelope when the cursor or depth is invalid.
    Does not read the filesystem or emit the section-listed event.
    """
    if expected_read_sha256 and not sha256_hex_equal(read_sha256, expected_read_sha256):
        return _stale_read_root(
            read_sha256=read_sha256,
            expected_read_sha256=expected_read_sha256,
        )
    depth, depth_err = _validate_max_depth(max_depth)
    if depth_err is not None:
        return depth_err
    start, offset_err = parse_lean_descriptor_offset(descriptor_offset, max_depth=depth)
    if offset_err is not None:
        return offset_err

    nav = _document_sections(text)
    if not any(s.level > 0 for s in nav):
        rows = [
            {
                "id": UNSECTIONED_ID,
                "level": 0,
                "char_count": len(text),
                "subtree_char_count": len(text),
            }
        ]
        depth_cut = 0
    else:
        rows, depth_cut = _build_lean_rows(text, nav, max_depth=depth)

    if start > len(rows):
        return invalid_cursor_envelope(
            message=(
                f"Lean listing cursor offset row {start} is out of range for "
                f"lean=true, max_depth={depth} ({len(rows)} rows)."
            ),
            max_depth_applied=depth,
            reason="offset_out_of_range",
            row_index=start,
        )

    paged, omitted, cursor, truncated = _page_lean_rows(rows, start=start, depth=depth)
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
    }
    if truncated and cursor is not None:
        out["_next"] = (
            f'fs(op="md_list", path="{path}", offset="{cursor}", lean=true, max_depth={depth})'
        )
    return out


def lean_listing_scope_error(
    *, offset: int | str, lean: bool, max_depth: int
) -> dict[str, Any]:
    """Refuse a lean cursor that arrived without a matching lean listing.

    Records mcp.tool.markdown.lean.invalid_cursor_scope and returns the
    invalid_cursor_scope envelope. Callers pass the offending offset plus the
    lean flag and max_depth they actually received.
    """
    record(
        "mcp.tool.markdown.lean.invalid_cursor_scope",
        reason="lean_required",
        lean=lean,
        max_depth=max_depth,
    )
    return {
        "code": "invalid_cursor_scope",
        "message": (
            f"md_list offset={offset!r} requires lean=true with a stable max_depth; "
            f"got lean={lean}, max_depth={max_depth}. Re-list from offset=0."
        ),
        "source": "fs",
        "retryable": False,
        "data": {},
    }


def lean_encoded_offset_requires_lean(
    *, offset: int | str, lean: bool
) -> dict[str, Any] | None:
    """Return a scope error when a lean cursor arrives with lean left off.

    Non-lean callers that pass a d{depth}:{row} string cannot page that
    cursor. Digit strings and plain ints return None so normal paging runs.
    """
    if (
        not lean
        and isinstance(offset, str)
        and lean_offset_pattern().match(offset.strip())
    ):
        return lean_listing_scope_error(offset=offset, lean=False, max_depth=0)
    return None


def _non_lean_string_offset_error(offset: object) -> dict[str, Any]:
    return {
        "code": "invalid_offset",
        "message": (
            f"offset={offset!r} is not a numeric index. "
            "Encoded string cursors (d{depth}:{row}) are valid only on "
            "md_list with lean=true."
        ),
        "source": "fs",
        "retryable": False,
        "data": {"reason": "string_offset_not_lean"},
    }


def contain_non_lean_offset(
    offset: int | str,
) -> tuple[int | None, dict[str, Any] | None]:
    """Coerce a non-lean fs offset to an integer index.

    Digit-only strings become ints so read and non-lean md_list keep the
    pre-union pydantic behavior. Lean cursors and any other string return an
    error envelope instead of reaching line or descriptor slicing. Returns
    ``(int, None)`` on success and ``(None, error)`` on refusal. Does not
    record an event; cursor-shaped strings reuse ``lean_listing_scope_error``.
    """
    if isinstance(offset, bool) or not isinstance(offset, (int, str)):
        return None, _non_lean_string_offset_error(offset)
    if isinstance(offset, int):
        return offset, None
    raw = offset.strip()
    if raw.isdigit():
        return int(raw), None
    if lean_offset_pattern().match(raw):
        return None, lean_listing_scope_error(offset=raw, lean=False, max_depth=0)
    return None, _non_lean_string_offset_error(raw)


def lean_list_sections(
    *,
    path: str,
    sandbox: str,
    text: str,
    read_sha256: str,
    max_depth: int,
    offset: int | str = 0,
    expected_read_sha256: str | None = None,
) -> dict[str, Any]:
    """List one lean page and record the section-listed event on success.

    Delegates parsing and paging to build_lean_document_manifest. Invalid
    cursor or depth envelopes pass through unchanged. Only a successful page
    emits mcp.tool.markdown.sections.listed.
    """
    manifest = build_lean_document_manifest(
        path=path,
        sandbox=sandbox,
        text=text,
        read_sha256=read_sha256,
        max_depth=max_depth,
        descriptor_offset=offset,
        expected_read_sha256=expected_read_sha256,
    )
    code = manifest.get("code")
    if code == "invalid_cursor_scope":
        return manifest
    if code == "invalid_max_depth":
        record(
            "mcp.tool.markdown.lean.invalid_max_depth",
            max_depth_requested=manifest.get("data", {}).get("max_depth_requested"),
        )
        return manifest
    record(
        "mcp.tool.markdown.sections.listed",
        path=path,
        sandbox=sandbox,
        count=len(manifest.get("rows") or []),
        lean=True,
        max_depth_applied=manifest.get("max_depth_applied"),
        depth_rows_omitted=manifest.get("depth_rows_omitted"),
        truncated=bool(manifest.get("truncated")),
    )
    return manifest


def pdf_lean_not_applied(
    *, path: str, sandbox: str, listing: dict[str, Any]
) -> dict[str, Any]:
    """Return a PDF listing unchanged when lean was requested but not applied.

    Outline navigation does not use the lean row shape. The payload sets
    lean_applied false and records mcp.tool.markdown.sections.listed with
    that fact so callers can see the request was ignored.
    """
    record(
        "mcp.tool.markdown.sections.listed",
        path=path,
        sandbox=sandbox,
        count=len(listing["sections"]),
        source=listing["source"],
        lean_requested=True,
        lean_applied=False,
    )
    return {
        "path": path,
        "sandbox": sandbox,
        "lean_requested": True,
        "lean_applied": False,
        "lean_reason": "pdf_outline",
        **listing,
    }
