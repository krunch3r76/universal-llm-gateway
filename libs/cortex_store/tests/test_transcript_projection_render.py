"""Offline tests for transcript_projection_render (P8, AC-10)."""

from __future__ import annotations

import pytest

from cortex_store.transcript_projection_render import (
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
