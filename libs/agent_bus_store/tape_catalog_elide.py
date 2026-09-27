"""Drop IDE catalog turns and elide harness blocks before tape budget measurement."""

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

# Whole-turn harness injection (Cursor IDE catalog user messages). A turn whose
# body is only these blocks is not speech — drop it. Mixed turns that also
# carry a substantive ``<user_query>`` stay, and these blocks are not elided
# there: the operator text and the catalog share one message.
_CATALOG_TURN_TAGS = (
    "available_subagent_types",
    "available_subagent_models",
    "dynamic_tools",
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


def _is_ide_catalog_turn(content: str) -> bool:
    """True when ``content`` is only IDE catalog blocks and whitespace.

    Requires at least one catalog-turn tag so a checkpoint command or other
    harness text without these tags is left for the block elider.
    """
    if not any(f"<{tag}>" in content for tag in _CATALOG_TURN_TAGS):
        return False
    if any(substantive for _start, _end, substantive in _user_query_spans(content)):
        return False
    residual = content
    for tag in _CATALOG_TURN_TAGS:
        residual = _tag_block_re(tag).sub("", residual)
    return not residual.strip()


def exclude_ide_catalog_turns(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop user turns that are IDE catalog injections, not operator speech."""
    try:
        kept: list[dict[str, Any]] = []
        for message in messages:
            if str(message.get("role") or "") != "user":
                kept.append(message)
                continue
            content = message.get("content")
            if isinstance(content, str) and content and _is_ide_catalog_turn(content):
                continue
            kept.append(message)
        return kept
    except Exception:
        return messages


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


__all__ = ["elide_ide_catalog_blocks", "exclude_ide_catalog_turns"]
