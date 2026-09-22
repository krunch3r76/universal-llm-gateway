"""Assemble R6 operator-hop-harvest view and enforce size cap."""

from __future__ import annotations

import json
from typing import Any


def assemble_operator_hop_view(
    *,
    worker_thread: dict[str, object],
    summoning_thread: dict[str, object],
    conductor: dict[str, object],
    scoreboard: dict[str, object],
    wait: dict[str, object],
    job: dict[str, object] | None,
    harvest_recipe: dict[str, object],
    continuation: dict[str, object],
    next_admit_divergent: bool,
) -> dict[str, object]:
    """Return R6 top-level JSON object."""
    return {
        "worker_thread": worker_thread,
        "summoning_thread": summoning_thread,
        "conductor": conductor,
        "scoreboard": scoreboard,
        "wait": wait,
        "job": job,
        "harvest_recipe": harvest_recipe,
        "continuation": continuation,
        "next_admit_divergent": next_admit_divergent,
    }


def cap_view_json_bytes(view: dict[str, Any], *, max_bytes: int = 4096) -> dict[str, Any]:
    """Truncate large string fields until serialized UTF-8 fits max_bytes."""
    encoded = json.dumps(view, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(encoded) <= max_bytes:
        return view
    trimmed = dict(view)
    for key in ("scoreboard", "conductor", "harvest_recipe"):
        block = trimmed.get(key)
        if isinstance(block, dict) and "rows" in block and isinstance(block["rows"], list):
            block = dict(block)
            block["rows"] = block["rows"][:8]
            block["_truncated"] = True
            trimmed[key] = block
    encoded = json.dumps(trimmed, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    if len(encoded) <= max_bytes:
        return trimmed
    trimmed["_size_truncated"] = True
    return trimmed
