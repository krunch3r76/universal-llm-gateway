"""Project CDP frontier row byte economics for work-key ledger."""

from __future__ import annotations

from typing import Any


def frontier_row_economics(row: dict[str, Any]) -> dict[str, Any]:
    """Return CDP substrate quantities when ``address.kind`` is ``cdp``."""
    rec = row.get("record_json")
    if isinstance(rec, str):
        import json

        try:
            rec = json.loads(rec)
        except json.JSONDecodeError:
            return {}
        if not isinstance(rec, dict):
            return {}
    elif not isinstance(rec, dict):
        return {}
    address = rec.get("address") if isinstance(rec.get("address"), dict) else {}
    if address.get("kind") != "cdp":
        return {}
    prompt_bytes = rec.get("prompt_bytes")
    harvest_chars = rec.get("harvest_chars")
    started = row.get("started_at")
    terminal = row.get("terminal_at")
    wall_s: float | None = None
    if started and terminal:
        try:
            from datetime import datetime

            def _parse(stamp: str) -> datetime:
                if stamp.endswith("Z"):
                    stamp = stamp[:-1] + "+00:00"
                return datetime.fromisoformat(stamp)

            wall_s = (_parse(str(terminal)) - _parse(str(started))).total_seconds()
        except (TypeError, ValueError):
            wall_s = None
    out: dict[str, Any] = {"substrate": "cdp"}

    def _as_int(val: object) -> int | None:
        try:
            return int(val)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    if prompt_bytes is not None:
        parsed = _as_int(prompt_bytes)
        if parsed is not None:
            out["prompt_bytes"] = parsed
    if harvest_chars is not None:
        parsed = _as_int(harvest_chars)
        if parsed is not None:
            out["harvest_chars"] = parsed
    if wall_s is not None:
        out["wall_s"] = wall_s
    return out
