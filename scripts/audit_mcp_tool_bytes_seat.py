"""Per-seat byte measurement and event frequency join for audit-mcp-tool-bytes."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = Path(sys.executable)
QUERY_EVENTS = REPO_ROOT / "scripts" / "query-events"

SEAT_LABEL = {"life": "user-vortex-life", "code": "user-vortex-code"}


def fetch_call_counts() -> tuple[str | None, str | None, dict[tuple[str, str], int]]:
    """Return (oldest_ts, newest_ts, {(surface, tool_name): calls})."""
    sql = (
        "SELECT MIN(timestamp) AS oldest, MAX(timestamp) AS newest "
        "FROM events WHERE signal='mcp.request.completed' "
        "AND json_extract(payload, '$.tool_name') IS NOT NULL"
    )
    meta = _query_sql(sql)
    oldest = meta["rows"][0]["oldest"] if meta.get("rows") else None
    newest = meta["rows"][0]["newest"] if meta.get("rows") else None

    sql2 = (
        "SELECT json_extract(payload, '$.surface') AS surface, "
        "json_extract(payload, '$.tool_name') AS tool_name, COUNT(*) AS calls "
        "FROM events WHERE signal='mcp.request.completed' "
        "AND json_extract(payload, '$.tool_name') IS NOT NULL "
        "GROUP BY 1, 2"
    )
    counts: dict[tuple[str, str], int] = {}
    for row in _query_sql(sql2).get("rows", []):
        surface = row.get("surface")
        tool = row.get("tool_name")
        if surface and tool:
            counts[(str(surface), str(tool))] = int(row["calls"])
    return oldest, newest, counts


def _query_sql(sql: str) -> dict[str, Any]:
    proc = subprocess.run(
        [str(PYTHON), str(QUERY_EVENTS), "--sql", sql, "--compact"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    return json.loads(proc.stdout)


def build_ranked_rows(
    seat_rows: dict[str, list[dict[str, Any]]],
    call_counts: dict[tuple[str, str], int],
) -> list[dict[str, Any]]:
    """Merge wire bytes with observed call frequency per (tool, seat)."""
    merged: list[dict[str, Any]] = []
    for surface, rows in seat_rows.items():
        seat = SEAT_LABEL[surface]
        for row in rows:
            tool = str(row["name"])
            wire = int(row["total_b"])
            calls = call_counts.get((surface, tool), 0)
            bpc = wire / calls if calls else None
            freq = "observed" if calls else "zero_calls"
            merged.append(
                {
                    "tool": tool,
                    "seat": seat,
                    "surface": surface,
                    "wire_bytes": wire,
                    "calls_observed": calls,
                    "frequency_status": freq,
                    "bytes_per_call": bpc,
                }
            )
    merged.sort(
        key=lambda r: (r["bytes_per_call"] is None, -(r["bytes_per_call"] or 0)),
    )
    return merged
