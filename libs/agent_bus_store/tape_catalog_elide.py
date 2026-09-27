"""Elide IDE harness catalog blocks before tape budget measurement."""

from __future__ import annotations

import re
from typing import Any

# Structural allowlist — unknown tags pass through (fail-open at parse boundary).
_ELIDE_TAG_NAMES = (
    "cursor_commands",
    "manually_attached_skills",
    "dynamic_tool_catalog",
    "open_and_recently_viewed_files",
)

_USER_QUERY_RE = re.compile(
    r"<user_query>\s*(.*?)\s*</user_query>",
    re.DOTALL | re.IGNORECASE,
)


def _tag_block_re(tag: str) -> re.Pattern[str]:
    return re.compile(
        rf"<{re.escape(tag)}>\s*(.*?)\s*</{re.escape(tag)}>",
        re.DOTALL | re.IGNORECASE,
    )


def _user_query_spans(text: str) -> list[tuple[int, int, bool]]:
    """Every ``<user_query>`` span as ``(start, end, has_substance)``."""
    return [
        (match.start(), match.end(), bool(match.group(1).strip()))
        for match in _USER_QUERY_RE.finditer(text)
    ]


def _spans_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def _elide_catalog_in_content(content: str) -> str:
    """Single pass over ``content`` — spans never drift because nothing is rebuilt mid-scan."""
    uq_spans = _user_query_spans(content)
    has_uq = any(substantive for _start, _end, substantive in uq_spans)

    replacements: list[tuple[int, int, str]] = []
    for tag in _ELIDE_TAG_NAMES:
        if tag == "cursor_commands" and not has_uq:
            continue
        for match in _tag_block_re(tag).finditer(content):
            start, end = match.span()
            if any(
                _spans_overlap(start, end, uq_start, uq_end)
                for uq_start, uq_end, _substantive in uq_spans
            ):
                continue
            byte_count = len(match.group(0).encode("utf-8"))
            replacements.append((start, end, f"[elided {tag}: {byte_count} B]"))
    if not replacements:
        return content
    replacements.sort()
    pieces: list[str] = []
    cursor = 0
    for start, end, marker in replacements:
        if start < cursor:
            continue
        pieces.append(content[cursor:start])
        pieces.append(marker)
        cursor = end
    pieces.append(content[cursor:])
    return "".join(pieces)


def elide_ide_catalog_blocks(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return copies with allowlisted harness spans replaced by one-line markers."""
    try:
        out: list[dict[str, Any]] = []
        for message in messages:
            if str(message.get("role") or "") != "user":
                out.append(message)
                continue
            content = message.get("content")
            if not isinstance(content, str) or not content:
                out.append(message)
                continue
            elided = _elide_catalog_in_content(content)
            if elided == content:
                out.append(message)
            else:
                out.append({**message, "content": elided})
        return out
    except Exception:
        return messages


__all__ = ["elide_ide_catalog_blocks"]
