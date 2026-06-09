"""Pure line-range slicing for decoded text file reads."""

from __future__ import annotations


def apply_line_range(
    text: str, offset: int, limit: int
) -> tuple[str, dict[str, object]]:
    """Return a line window from *text* and range metadata for the MCP payload."""
    lines = text.split("\n")
    total = len(lines)
    start = min(offset, total)
    end = total if limit == 0 else min(offset + limit, total)
    window = lines[start:end]
    content = "\n".join(window)
    returned = len(window)
    return content, {
        "line_range": {"offset": offset, "limit": limit, "returned": returned},
        "total_lines": total,
        "truncated": (start + returned) < total,
    }
