"""CDP frontier row economics projection (a:43747 D6)."""

from __future__ import annotations

import json

from services.git_integration_worker.frontier_row_economics import (
    frontier_row_economics,
)


def test_cdp_row_projects_substrate_and_quantities() -> None:
    rec = {
        "address": {"kind": "cdp", "thread_id": "23296"},
        "prompt_bytes": 1200,
        "harvest_chars": 800,
    }
    row = {
        "record_json": json.dumps(rec),
        "started_at": "2026-11-12T10:00:00Z",
        "terminal_at": "2026-11-12T10:05:00Z",
    }
    out = frontier_row_economics(row)
    assert out["substrate"] == "cdp"
    assert out["prompt_bytes"] == 1200
    assert out["harvest_chars"] == 800
    assert out["wall_s"] == 300.0


def test_open_cdp_row_has_no_wall_s() -> None:
    rec = {"address": {"kind": "cdp"}, "prompt_bytes": 10}
    row = {"record_json": rec, "started_at": "2026-11-12T10:00:00Z"}
    out = frontier_row_economics(row)
    assert "wall_s" not in out


def test_non_cdp_row_projects_nothing() -> None:
    row = {"record_json": {"address": {"kind": "api_role"}}}
    assert frontier_row_economics(row) == {}
