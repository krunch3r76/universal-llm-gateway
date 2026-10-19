"""Shape A AC3 — compact folded task guidance on hop >= 1."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from implement_admission.conductor_materialize import ConductorMaterializeContext
from implement_admission.conductor_score_journal import render_sparse_scoreboard
from implement_admission.conductor_task_guidance import (
    closed_row_stop_paragraph_absent_in,
    render_compact_folded_view,
    render_task_guidance,
    seats_from_roles,
)


@pytest.mark.offline
def test_compact_view_hop() -> None:
    slug = "compact-hop"
    tip = render_sparse_scoreboard(
        source_ref="todo:compact-hop",
        slug=slug,
        entry_gate="G2",
        stop_after=None,
        rows=("G1", "G2", "G3"),
    )
    tip = tip.replace("| G1 |", "| G1 |").replace(
        "| G1 | G1 scope | plan | DONE | landed on master |",
        "| G1 | G1 scope | plan | DONE | landed on master |",
    )
    lines = tip.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("| G1 |"):
            parts = line.split("|")
            if len(parts) > 5:
                parts[4] = " DONE "
                parts[5] = " landed on master "
                lines[i] = "|".join(parts)
    tip = "\n".join(lines)
    compact = render_compact_folded_view(tip)
    assert "tip_sha256:" in compact
    assert closed_row_stop_paragraph_absent_in(compact)
    ctx = ConductorMaterializeContext(
        source_ref="todo:compact-hop",
        slug=slug,
        name="compact",
        density_triage=None,
        derived_from=None,
        stop_after=None,
        entry_gate="G2",
        summon_mode="headless",
        problem=None,
        scope=None,
        acceptance=None,
    )
    with patch(
        "implement_admission.conductor_task_guidance.render_sparse_scoreboard"
    ) as sparse:
        sparse.side_effect = AssertionError("sparse must not run on hop>=1")
        body = render_task_guidance(
            ctx, seats=seats_from_roles(), hop_seq=2, tip_body=tip
        )
    assert "tip_sha256:" in body
    assert closed_row_stop_paragraph_absent_in(body)
