"""Stdlib-only fence pairing: open/close matching for markdown section navigators."""

from __future__ import annotations

import re

__all__ = (
    "MIN_FENCE_LEN",
    "is_closing_fence_line",
    "is_fence_line",
    "parse_fence_open",
)

_FENCE_LINE = re.compile(r"^(`+|~+)(\w*)$")

# CommonMark — and section navigators backing fs(op="md_list"/"md_read") —
# only recognize a fence of three or more delimiters. Shorter runs render as
# literal text and must not flip fence state.
MIN_FENCE_LEN = 3


def is_closing_fence_line(line: str, fence_char: str, need: int) -> bool:
    """True when *line* is an exact-length closing fence for *fence_char* × *need*."""
    return line.strip() == fence_char * need


def is_fence_line(line: str) -> bool:
    """True when *line* is a fence opener/closer line (backtick or tilde run)."""
    return _FENCE_LINE.match(line.strip()) is not None


def parse_fence_open(line: str) -> tuple[str, int, str] | None:
    """Return (char, length, info_string) when *line* opens a fence, else None."""
    m = _FENCE_LINE.match(line.strip())
    if not m:
        return None
    chars, lang = m.group(1), m.group(2)
    return chars[0], len(chars), lang
