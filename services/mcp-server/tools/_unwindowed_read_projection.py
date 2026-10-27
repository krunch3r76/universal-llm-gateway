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


def _escape_path_for_fs_call(path: str) -> str:
    return path.replace("\\", "\\\\").replace('"', '\\"')


def _md_read_call(path: str, section_path: str) -> str:
    escaped_path = _escape_path_for_fs_call(path)
    escaped_section = section_path.replace("\\", "\\\\").replace('"', '\\"')
    return f'fs(op="md_read", path="{escaped_path}", section="{escaped_section}")'


def _read_call(path: str, offset: int, limit: int) -> str:
    escaped_path = _escape_path_for_fs_call(path)
    return f'fs(op="read", path="{escaped_path}", offset={offset}, limit={limit})'


def _is_markdown_file(request_path: str, text: str) -> bool:
    if request_path.lower().endswith(".md"):
        return True
    if not text.strip():
        return False
    try:
        sections = md_list_sections(text)
    except SectionError:
        return False
    return any(int(row.get("level", 0)) >= 1 for row in sections)


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
    except SectionError as exc:
        return build_plain_unwindowed_payload(request_path=request_path, text=text)

    sections = _normalize_section_rows(raw_sections)
    if not sections:
        return build_plain_unwindowed_payload(request_path=request_path, text=text)

    first_path = sections[0]["path"]
    try:
        first_body = md_read_section(text, first_path)
    except SectionError:
        first_body = text[: min(len(text), DEFAULT_UNWINDOWED_MAX_BYTES)]

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
    return payload


def build_plain_unwindowed_payload(
    *,
    request_path: str,
    text: str,
) -> dict[str, Any]:
    """First line window plus the exact fs read call for the next window."""
    window_text, range_meta = apply_line_range(text, 0, DEFAULT_UNWINDOWED_MAX_LINES)
    encoded = window_text.encode("utf-8")
    if len(encoded) > DEFAULT_UNWINDOWED_MAX_BYTES:
        window_text = encoded[:DEFAULT_UNWINDOWED_MAX_BYTES].decode("utf-8", errors="ignore")
        line_range = dict(range_meta.get("line_range") or {})
        line_range["returned"] = len(window_text.split("\n")) if window_text else 0
        range_meta = dict(range_meta)
        range_meta["line_range"] = line_range

    total_lines = len(text.split("\n"))
    returned = int((range_meta.get("line_range") or {}).get("returned") or 0)
    next_offset = returned if returned < total_lines else None
    next_read = (
        _read_call(request_path, next_offset, DEFAULT_UNWINDOWED_MAX_LINES)
        if next_offset is not None
        else None
    )

    payload: dict[str, Any] = {
        "content": window_text,
        "unwindowed_oversized": True,
        "kind": "line_window",
        "total_lines": total_lines,
        "line_range": range_meta.get("line_range"),
        "prefer_windowed_reads": True,
        "_next": (
            "Unwindowed read exceeded the default cap; use offset/limit for further lines."
        ),
    }
    if next_read:
        payload["next_read"] = next_read
        payload["next_offset"] = next_offset
    return payload


def build_unwindowed_oversized_payload(
    *,
    request_path: str,
    text: str,
) -> dict[str, Any]:
    """Route markdown-shaped files to section listing; otherwise line windows."""
    if _is_markdown_file(request_path, text):
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
    section_reads = [
        _md_read_call(sidecar_uri, str(row["path"]))
        for row in sections
        if str(row.get("path", "")).strip()
    ]
    return {
        "sidecar_uri": sidecar_uri,
        "total_chars": len(text),
        "sections": sections,
        "section_reads": section_reads,
    }
