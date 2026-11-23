"""Encode and parse self-describing lean md_list continuation offsets."""

from __future__ import annotations

import re
from typing import Any

from mcp_events import record

_LEAN_OFFSET_RE = re.compile(r"^d([1-6]):(\d+)$")


def encode_lean_descriptor_offset(depth: int, row_index: int) -> str:
    return f"d{int(depth)}:{int(row_index)}"


def lean_offset_pattern() -> re.Pattern[str]:
    return _LEAN_OFFSET_RE


def invalid_cursor_envelope(
    *,
    message: str,
    max_depth_applied: int,
    reason: str,
    **extra: Any,
) -> dict[str, Any]:
    data: dict[str, Any] = {"lean": True, "max_depth_applied": max_depth_applied, "reason": reason}
    data.update(extra)
    record(
        "mcp.tool.markdown.lean.invalid_cursor_scope",
        max_depth_applied=max_depth_applied,
        reason=reason,
    )
    return {
        "code": "invalid_cursor_scope",
        "message": message,
        "source": "fs",
        "retryable": False,
        "data": data,
    }


def parse_lean_descriptor_offset(
    offset: int | str,
    *,
    max_depth: int,
) -> tuple[int | None, dict[str, Any] | None]:
    depth = int(max_depth)
    if isinstance(offset, str):
        raw = offset.strip()
        if not raw:
            return None, invalid_cursor_envelope(
                message="Lean listing offset must be 0 or an encoded cursor d{depth}:{row}.",
                max_depth_applied=depth,
                reason="malformed_offset",
            )
        match = _LEAN_OFFSET_RE.match(raw)
        if not match:
            return None, invalid_cursor_envelope(
                message=(
                    f"Malformed lean cursor {raw!r}; expected d{{depth}}:{{row}} with depth 1–6."
                ),
                max_depth_applied=depth,
                reason="malformed_offset",
                offset=raw,
            )
        enc_depth, row_index = int(match.group(1)), int(match.group(2))
        if enc_depth != depth:
            return None, invalid_cursor_envelope(
                message=(
                    f"Lean listing cursor {raw!r} was issued at max_depth={enc_depth}; "
                    f"got max_depth={depth}. Pass the same max_depth or restart from offset=0."
                ),
                max_depth_applied=depth,
                reason="depth_mismatch",
                cursor_depth=enc_depth,
            )
        return row_index, None
    row = int(offset)
    if row < 0:
        return None, invalid_cursor_envelope(
            message=f"Lean listing offset must be >= 0, got {row}.",
            max_depth_applied=depth,
            reason="negative_offset",
        )
    if row > 0:
        return None, invalid_cursor_envelope(
            message=(
                f"Lean continuation offset={row} must use encoded cursor d{{depth}}:{{row}} "
                "from the prior lean page (cursor field or _next)."
            ),
            max_depth_applied=depth,
            reason="bare_int_continuation",
        )
    return 0, None
