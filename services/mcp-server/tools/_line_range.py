"""Pure line-range slicing for decoded text file reads."""

from __future__ import annotations

DEFAULT_UNWINDOWED_MAX_LINES = 800
DEFAULT_UNWINDOWED_MAX_BYTES = 32_768


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


def exceeds_unwindowed_read_cap(text: str) -> bool:
    """True when a full-file read should be capped for model-facing payloads."""
    if len(text.encode("utf-8")) > DEFAULT_UNWINDOWED_MAX_BYTES:
        return True
    return len(text.split("\n")) > DEFAULT_UNWINDOWED_MAX_LINES


def apply_unwindowed_read_cap(text: str) -> tuple[str, dict[str, object]]:
    """Cap offset=0 limit=0 reads at :data:`DEFAULT_UNWINDOWED_MAX_*`."""
    content, meta = apply_line_range(text, 0, DEFAULT_UNWINDOWED_MAX_LINES)
    encoded = content.encode("utf-8")
    if len(encoded) > DEFAULT_UNWINDOWED_MAX_BYTES:
        content = encoded[:DEFAULT_UNWINDOWED_MAX_BYTES].decode("utf-8", errors="ignore")
        meta = dict(meta)
        line_range = dict(meta.get("line_range") or {})
        line_range["returned"] = len(content.split("\n")) if content else 0
        meta["line_range"] = line_range
    total_lines = len(text.split("\n"))
    returned = int((meta.get("line_range") or {}).get("returned") or 0)
    meta["total_lines"] = total_lines
    meta["truncated"] = returned < total_lines or len(text.encode("utf-8")) > len(
        content.encode("utf-8")
    )
    if meta["truncated"]:
        meta["next_offset"] = returned
    return content, meta
