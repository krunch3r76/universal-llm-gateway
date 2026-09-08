"""Hub WATERMARK parsing for continuity consolidate pipelines."""

from __future__ import annotations

import re
from typing import Any

SEEDED_BY = "continuity-consolidate"
WATERMARK_PREFIX = "WATERMARK: consolidated_through="
_WATERMARK_RE = re.compile(
    r"consolidated_through=(?P<thread>[0-9a-zA-Z_-]+)#(?P<turn>\d+)"
)


def hub_entity_id(root_thread: str) -> str:
    """Cortex hub card for a continuity root house."""
    return f"document:{root_thread}-continuity"


def parse_watermark(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Newest pipeline-authored WATERMARK row, decoded."""
    for row in sorted(rows, key=lambda r: int(r.get("id") or 0), reverse=True):
        if row.get("seeded_by") != SEEDED_BY:
            continue
        claim = str(row.get("claim") or "")
        match = _WATERMARK_RE.search(claim)
        if claim.startswith(WATERMARK_PREFIX) and match:
            return {
                "assertion_id": row.get("id"),
                "thread": match.group("thread"),
                "turn": int(match.group("turn")),
                "claim": claim,
            }
    return None


__all__ = [
    "SEEDED_BY",
    "WATERMARK_PREFIX",
    "hub_entity_id",
    "parse_watermark",
]
