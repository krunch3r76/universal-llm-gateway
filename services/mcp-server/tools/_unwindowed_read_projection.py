"""Addressable payloads for unwindowed fs reads over the default size cap.

Replaces silent line/byte truncation with section listings (markdown) or an
explicit next-window read call (plain text).
"""

from __future__ import annotations

from typing import Any

from markdown_sections import SectionError
from markdown_sections import list_sections as md_list_sections
from markdown_sections import read_section as md_read_section

from ._line_range import (
    DEFAULT_UNWINDOWED_MAX_BYTES,
    DEFAULT_UNWINDOWED_MAX_LINES,
    apply_line_range,
)

MAX_LISTED_SECTIONS = 200


def _escape_path_for_fs_call(path: str) -> str:
    return path.replace("\\", "\\\\").replace('"', '\\"')


def _md_read_call(path: str, section_path: str) -> str:
    escaped_path = _escape_path_for_fs_call(path)
    escaped_section = section_path.replace("\\", "\\\\").replace('"', '\\"')
    return f'fs(op="md_read", path="{escaped_path}", section="{escaped_section}")'


def _read_call(path: str, offset: int, limit: int) -> str:
    escaped_path = _escape_path_for_fs_call(path)
    return f'fs(op="read", path="{escaped_path}", offset={offset}, limit={limit})'


def _is_markdown_file(request_path: str) -> bool:
    lowered = request_path.lower()
    return lowered.endswith(".md") or lowered.endswith(".markdown")


def _cap_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def _lines_returned_in_window(window_text: str, *, single_line_partial: bool) -> int:
    if not window_text or single_line_partial:
        return 0
    parts = window_text.split("\n")
    if window_text.endswith("\n"):
        return max(len(parts) - 1, 0)
    return len(parts)


def _cap_utf8_on_line_boundary(text: str, max_bytes: int) -> tuple[str, bool, bool]:
    """Cap *text* to *max_bytes*, ending on a complete line when possible.

    Returns ``(capped, byte_limited, single_line_partial)``. *single_line_partial*
    is true when the cap falls on one long line with no interior newline.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False, False
    partial = encoded[:max_bytes].decode("utf-8", errors="ignore")
    last_nl = partial.rfind("\n")
    if last_nl >= 0:
        capped = partial[: last_nl + 1]
        while capped and len(capped.encode("utf-8")) > max_bytes:
            prev = capped.rfind("\n", 0, max(0, len(capped) - 1))
            if prev < 0:
                capped = ""
                break
            capped = capped[: prev + 1]
        return capped, True, False
    return partial, True, True


def _normalize_section_rows(rows: list[dict[str, str | int]]) -> list[dict[str, Any]]:
    return [
        {
            "heading": str(row.get("heading", "")),
            "level": int(row.get("level", 0)),
            "path": str(row.get("path", "")),
            "line": int(row.get("line", 0)),
            "chars": int(row.get("chars", 0)),
        }
        for row in rows
    ]


def build_markdown_unwindowed_payload(
    *,
    request_path: str,
    text: str,
) -> dict[str, Any]:
    """Listing, first section body, and md_read calls — no truncated file body."""
    try:
        raw_sections = md_list_sections(text)
    except SectionError:
        return build_plain_unwindowed_payload(request_path=request_path, text=text)

    sections = _normalize_section_rows(raw_sections)
    if not sections:
        return build_plain_unwindowed_payload(request_path=request_path, text=text)

    sections_truncated = len(sections) > MAX_LISTED_SECTIONS
    if sections_truncated:
        sections = sections[:MAX_LISTED_SECTIONS]

    first_path = sections[0]["path"]
    try:
        first_raw = md_read_section(text, first_path)
    except SectionError:
        first_raw = text
    first_body, first_byte_limited, first_single_line_partial = _cap_utf8_on_line_boundary(
        first_raw, DEFAULT_UNWINDOWED_MAX_BYTES
    )

    section_reads = [
        _md_read_call(request_path, str(row["path"]))
        for row in sections[1:]
        if str(row.get("path", "")).strip()
    ]

    payload: dict[str, Any] = {
        "content": first_body,
        "unwindowed_oversized": True,
        "kind": "markdown_sections",
        "total_chars": len(text),
        "sections": sections,
        "first_section": {
            "heading": sections[0]["heading"],
            "path": sections[0]["path"],
            "line": sections[0]["line"],
        },
        "section_reads": section_reads,
        "prefer_selective_reads": True,
        "_next": (
            "Unwindowed read exceeded the default cap; full file is not inline. "
            "Use section_reads (or fs md_list / md_read) for remaining sections."
        ),
    }
    if sections_truncated:
        payload["sections_truncated"] = True
        payload["section_reads"].insert(
            0, f'fs(op="md_list", path="{_escape_path_for_fs_call(request_path)}")'
        )
    if first_byte_limited:
        payload["truncated"] = True
        payload["first_section_truncated"] = True
        payload["first_section_line_aligned"] = not first_single_line_partial
    return payload


def build_plain_unwindowed_payload(
    *,
    request_path: str,
    text: str,
) -> dict[str, Any]:
    """First line window plus the exact fs read call for the next window."""
    window_text, range_meta = apply_line_range(text, 0, DEFAULT_UNWINDOWED_MAX_LINES)
    window_text, byte_limited, single_line_partial = _cap_utf8_on_line_boundary(
        window_text, DEFAULT_UNWINDOWED_MAX_BYTES
    )
    line_range = dict(range_meta.get("line_range") or {})
    line_range["returned"] = _lines_returned_in_window(
        window_text, single_line_partial=single_line_partial
    )
    range_meta = dict(range_meta)
    range_meta["line_range"] = line_range

    total_lines = len(text.split("\n"))
    returned = int(line_range.get("returned") or 0)
    window_bytes = len(window_text.encode("utf-8"))
    full_bytes = len(text.encode("utf-8"))
    has_more = returned < total_lines or window_bytes < full_bytes
    next_offset: int | None = None
    if has_more:
        next_offset = returned

    payload: dict[str, Any] = {
        "content": window_text,
        "unwindowed_oversized": True,
        "kind": "line_window",
        "total_lines": total_lines,
        "line_range": line_range,
        "prefer_windowed_reads": True,
        "_next": (
            "Unwindowed read exceeded the default cap; use offset/limit for further lines."
        ),
    }
    if has_more or byte_limited:
        payload["truncated"] = True
    if has_more and next_offset is not None:
        payload["next_read"] = _read_call(
            request_path, next_offset, DEFAULT_UNWINDOWED_MAX_LINES
        )
        payload["next_offset"] = next_offset
    if single_line_partial or (window_bytes < full_bytes and returned >= total_lines):
        payload["byte_truncated"] = True
    return payload


def build_unwindowed_oversized_payload(
    *,
    request_path: str,
    text: str,
) -> dict[str, Any]:
    """Route markdown file extensions to section listing; otherwise line windows."""
    if _is_markdown_file(request_path):
        return build_markdown_unwindowed_payload(request_path=request_path, text=text)
    return build_plain_unwindowed_payload(request_path=request_path, text=text)


def sidecar_section_listing(
    *,
    sidecar_uri: str,
    text: str,
) -> dict[str, Any]:
    """md_list-shaped listing and follow-up md_read calls for a sidecar body."""
    try:
        raw_sections = md_list_sections(text)
    except SectionError as exc:
        return {"error": str(exc), "sidecar_uri": sidecar_uri}

    sections = _normalize_section_rows(raw_sections)
    sections_total = len(sections)
    listing_truncated = sections_total > MAX_LISTED_SECTIONS
    if listing_truncated:
        sections = sections[:MAX_LISTED_SECTIONS]
    section_reads = [
        _md_read_call(sidecar_uri, str(row["path"]))
        for row in sections
        if str(row.get("path", "")).strip()
    ]
    payload: dict[str, Any] = {
        "sidecar_uri": sidecar_uri,
        "total_chars": len(text),
        "sections": sections,
        "section_reads": section_reads,
        "sections_total": sections_total,
        "listing_truncated": listing_truncated,
    }
    return payload
