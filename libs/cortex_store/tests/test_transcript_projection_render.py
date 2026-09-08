"""Offline tests for transcript_projection_render (P8, AC-10)."""

from __future__ import annotations

import pytest

from cortex_store.transcript_projection_render import (
    cp_cells_by_ordinal,
    derive_open_interval,
    extract_operator_notes,
    render_projection_markdown,
    sha256_text,
)


@pytest.mark.offline
def test_p8_render_budget_and_operator_notes() -> None:
    windows = {}
    for i in range(30):
        tid = f"{i:08d}-0000-0000-0000-000000000001"
        windows[tid] = {
            "tab_title": f"tab-{i}",
            "lane": "10223",
            "binding": "dominant_write",
            "turn_count": 4,
            "source": "live",
            "jsonl": {"mtime": f"2026-09-08T{i:02d}:00:00+00:00"},
            "cells": [
                {
                    "turn_lo": 0,
                    "turn_hi": j + 1,
                    "note": f"note-{i}-{j}",
                    "projected_at": f"2026-09-08T{i:02d}:0{j}:00+00:00",
                    "facts": {"artifacts": [], "dispatches": [], "bus_touch": {}},
                }
                for j in range(4)
            ],
        }
    state = {
        "thread": "10223",
        "updated_at": "2026-09-08",
        "runs": 1,
        "windows": windows,
        "anchor_mismatches": [],
        "last_run": {},
    }
    prior = (
        "<!-- transcript-projection v1 · derived -->\n"
        "# 10223 — transcript projection\n\n"
        "## Operator notes\n"
        "preserve me exactly\n"
    )
    sha = sha256_text("state")
    md, collapsed = render_projection_markdown(
        state,
        state_sha256=sha,
        render_budget_bytes=8_192,
        prior_md=prior,
    )
    assert md.startswith("<!-- transcript-projection v1")
    assert extract_operator_notes(md) == "preserve me exactly"
    assert len(md.encode("utf-8")) <= 8_192
    assert collapsed > 0
    assert md.count("|") >= 30


@pytest.mark.offline
def test_ac_15_2_cells_cp_ordinal_order() -> None:
    tid = "9c37637d-379f-467e-89fd-849f6950ee19"
    state = {
        "thread": "10223",
        "updated_at": "2026-09-08",
        "runs": 1,
        "windows": {
            tid: {
                "tab_title": "tab",
                "lane": "10223",
                "binding": "dominant_write",
                "turn_count": 10,
                "source": "live",
                "jsonl": {"mtime": "2026-09-08T00:00:00+00:00"},
                "cells": [
                    {
                        "turn_lo": 0,
                        "turn_hi": 3,
                        "note": "cp2",
                        "boundary": {
                            "kind": "CHECKPOINT",
                            "cp_ordinal": 2,
                            "turn_number": 155,
                        },
                    },
                    {
                        "turn_lo": 3,
                        "turn_hi": 6,
                        "note": "cp1",
                        "boundary": {
                            "kind": "CHECKPOINT",
                            "cp_ordinal": 1,
                            "turn_number": 150,
                        },
                    },
                ],
            }
        },
        "anchor_mismatches": [],
        "last_run": {},
    }
    ordered = cp_cells_by_ordinal(state)
    assert [row["cp_ordinal"] for row in ordered] == [1, 2]
    md, _ = render_projection_markdown(state, state_sha256=sha256_text("x"))
    assert "## Cells (cp_ordinal order)" in md
    assert "| 1 | 150 |" in md
    assert "| 2 | 155 |" in md
    assert len(ordered) == 2


@pytest.mark.offline
def test_ac_15_3_open_interval_matches_derived() -> None:
    tid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    state = {
        "thread": "10223",
        "updated_at": "2026-09-08",
        "runs": 1,
        "windows": {
            tid: {
                "tab_title": "tab",
                "lane": "10223",
                "binding": "dominant_write",
                "turn_count": 12,
                "source": "live",
                "jsonl": {"mtime": "2026-09-08T00:00:00+00:00"},
                "cells": [],
                "open_tail": {
                    "turn_lo": 8,
                    "turn_hi": 12,
                    "note": "open",
                },
            }
        },
        "anchor_mismatches": [],
        "last_run": {},
    }
    interval = derive_open_interval(state)
    assert interval == {
        "transcript_ids": [tid],
        "turns": 4,
        "windows": [
            {
                "transcript_id": tid,
                "turn_lo": 8,
                "turn_hi": 12,
                "turns": 4,
            }
        ],
    }
    md, _ = render_projection_markdown(state, state_sha256=sha256_text("y"))
    assert "## Open interval (derived)" in md
    assert "turns: 4" in md
    assert f"`{tid[:8]}…` · turns 8→12 unsealed (4 turns)" in md
