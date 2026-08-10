"""MCP fs serve-hook for fleet operating-state snapshot reads.

Life ``fs(op=read)`` of ``SNAPSHOT_URI`` must return ``serve_view`` output so
expired snapshots answer honest-empty instead of stale ``running[]`` rows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claude_bundles.what_is_running_view import SNAPSHOT_URI, serve_view

from ._hashing import sha256_hex_of_bytes
from ._line_range import apply_line_range

OPERATING_STATE_REL = "notes/system/operational/what-is-running.json"


def is_operating_state_snapshot(path: str) -> bool:
    """True when *path* is the fleet operating-state cortex snapshot."""
    return path.lstrip("/") == OPERATING_STATE_REL


def apply_operating_state_serve(
    path: str,
    src: Path,
    read_sha256: str,
    *,
    binary: bool = False,
    offset: int = 0,
    limit: int = 0,
) -> dict[str, Any] | None:
    """Return served snapshot payload, or None when *path* is not the snapshot."""
    if not is_operating_state_snapshot(path):
        return None

    raw_text = src.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        return None

    served = serve_view(parsed)
    content = json.dumps(served, indent=2, sort_keys=True) + "\n"
    range_requested = offset > 0 or limit > 0
    if range_requested:
        content, range_meta = apply_line_range(content, offset, limit)
    else:
        range_meta = {}

    served_sha256 = sha256_hex_of_bytes(content.encode("utf-8"))
    rel = path.lstrip("/")
    result: dict[str, Any] = {
        "content": content,
        "path": rel,
        "read_sha256": read_sha256,
        "served_sha256": served_sha256,
        "serve_view_applied": True,
        "snapshot_uri": SNAPSHOT_URI,
    }
    if binary:
        result["binary_served_as_text"] = True
    if range_requested:
        result.update(range_meta)
        result["line_range_applied"] = True
    if not range_requested and content == "":
        result["observation"] = (
            "Read succeeded; served operating-state view decoded to empty text."
        )
    result["_next"] = (
        "Observation provenance for the returned view: quote served_sha256, "
        "not read_sha256 — read_sha256 pins the on-disk artifact; "
        "serve_view may filter liveness by wall-clock expiry."
    )
    return result
