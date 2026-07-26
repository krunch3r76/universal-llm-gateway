"""Session-store fence API: choose delimiter, wrap/extract bodies, escalate length."""

from __future__ import annotations

from markdown_fence import (
    MIN_FENCE_LEN,
    is_closing_fence_line,
    is_fence_line,
    parse_fence_open,
)

__all__ = (
    "MIN_FENCE_LEN",
    "choose_fence_char",
    "closing_fence",
    "extract_fenced",
    "fence_length",
    "is_closing_fence_line",
    "is_fence_line",
    "longest_run",
    "opening_fence",
    "parse_fence_open",
    "wrap_fenced",
)


def longest_run(content: str, char: str) -> int:
    """Return the longest consecutive run of *char* anywhere in *content*."""
    best = 0
    cur = 0
    for ch in content:
        if ch == char:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def choose_fence_char(body: str) -> str:
    """Pick backtick or tilde so the wrap delimiter outruns body delimiter runs."""
    bt = longest_run(body, "`")
    td = longest_run(body, "~")
    return "~" if td >= bt else "`"


def _max_delimiter_presence(body: str, fence_char: str) -> int:
    inline = longest_run(body, fence_char)
    line_only = 0
    for line in body.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if all(c == fence_char for c in stripped):
            line_only = max(line_only, len(stripped))
            continue
        parsed = parse_fence_open(stripped)
        if parsed and parsed[0] == fence_char:
            line_only = max(line_only, parsed[1])
    return max(inline, line_only)


def fence_length(body: str, fence_char: str) -> int:
    """Return delimiter length ≥ MIN_FENCE_LEN that cannot appear inside *body*."""
    return max(MIN_FENCE_LEN, _max_delimiter_presence(body, fence_char) + 1)


def opening_fence(body: str, *, lang: str = "text") -> str:
    """Build the opening fence line (char × length + info string) for *body*."""
    ch = choose_fence_char(body)
    length = fence_length(body, ch)
    return f"{ch * length}{lang}"


def closing_fence(body: str) -> str:
    """Build the exact-length closing fence line matching ``opening_fence`` for *body*."""
    ch = choose_fence_char(body)
    length = fence_length(body, ch)
    return ch * length


def wrap_fenced(body: str, *, lang: str = "text") -> str:
    """Wrap *body* in a fence pair whose delimiter length escapes all inner runs."""
    open_line = opening_fence(body, lang=lang)
    close_line = closing_fence(body)
    return f"{open_line}\n{body}\n{close_line}"


def extract_fenced(block: str) -> str:
    """Return the inner body of a fenced *block*; raise if opener/closer is missing."""
    lines = block.split("\n")
    if not lines:
        return ""
    parsed = parse_fence_open(lines[0])
    if not parsed:
        raise ValueError("block does not start with a fence line")
    ch, need, _lang = parsed
    body_lines: list[str] = []
    for line in lines[1:]:
        if is_closing_fence_line(line, ch, need):
            break
        body_lines.append(line)
    else:
        raise ValueError("unclosed fence")
    return "\n".join(body_lines)
