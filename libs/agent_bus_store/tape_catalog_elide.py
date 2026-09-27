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


def _user_query_has_substance(text: str) -> bool:
    match = _USER_QUERY_RE.search(text)
    if not match:
        return False
    return bool(match.group(1).strip())


def _spans_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def _elide_catalog_in_content(content: str) -> str:
    uq_match = _USER_QUERY_RE.search(content)
    uq_span = (uq_match.start(), uq_match.end()) if uq_match else None
    has_uq = _user_query_has_substance(content)

    out = content
    for tag in _ELIDE_TAG_NAMES:
        pattern = _tag_block_re(tag)
        pos = 0
        while True:
            match = pattern.search(out, pos)
            if not match:
                break
            start, end = match.span()
            if uq_span and _spans_overlap(start, end, uq_span[0], uq_span[1]):
                pos = end
                continue
            if tag == "cursor_commands" and not has_uq:
                pos = end
                continue
            block = match.group(0)
            byte_count = len(block.encode("utf-8"))
            marker = f"[elided {tag}: {tag}, {byte_count} B]"
            out = out[:start] + marker + out[end:]
            pos = start + len(marker)
    return out


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
