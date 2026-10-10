"""DISPOSITION draft template for slice 1 (not posted). R9 slots, ≤2000 code points."""

from __future__ import annotations

from typing import Any

_MAX_CODEPOINTS = 2000


def draft_disposition(
    *,
    land_sha: str,
    restart_set: list[str],
    route: str = "dry_run",
    wall_time_s: float | None = None,
    nested_usage: dict[str, Any] | None = None,
) -> tuple[str, int, bool]:
    """Return (text, codepoint_count, within_cap)."""
    usage = nested_usage or {"input_tokens": None, "output_tokens": None}
    wall = "n/a" if wall_time_s is None else f"{wall_time_s:.1f}s"
    body = (
        f"VERDICT: dry_run restart plan for land {land_sha[:12]}\n"
        f"PLANE: coordinator slice-1 (no sync_restart)\n"
        f"restart_set: {', '.join(sorted(restart_set)) or '(empty)'}\n"
        f"R9 route: {route}\n"
        f"R9 wall_time: {wall}\n"
        f"R9 nested_usage: in={usage.get('input_tokens')} out={usage.get('output_tokens')}\n"
    )
    chars = len(body)
    within = chars <= _MAX_CODEPOINTS
    if not within:
        body += "\n(spill: full quotes belong in sidecar_content)\n"
    return body, chars, within
