"""Tests for scoreboard genre detection and tip-status projection."""

from __future__ import annotations

from implement_admission.scoreboard_genre import (
    detect_genre,
    fold_row_lines,
    ordered_row_ids,
    project_row_status,
)

G_TABLE = "\n".join(
    [
        "## Gated lane",
        "",
        "| ID | Deliverable | Mode | Status | Stops |",
        "|---|---|---|---|---|",
        "| G1 | Frame | plan | DONE | |",
        "| G2 | Implement | agent | OPEN | |",
    ]
)

R_TABLE = "\n".join(
    [
        "## Rows",
        "",
        "| # | row | work_key | status | evidence | next |",
        "|---|---|---|---|---|---|",
        "| R1 | checkpoint leg | todo:a | **DONE** | landed | R2 |",
        "| R2 | liaison substrate | agent-bus:1 | OPEN | | — |",
    ]
)


def test_detect_genre_g_ladder() -> None:
    assert detect_genre(G_TABLE) == "g_ladder"


def test_detect_genre_r_ledger() -> None:
    assert detect_genre(R_TABLE) == "r_ledger"


def test_detect_genre_none_for_empty() -> None:
    assert detect_genre("# Charter\n\n## Next pickup\n\n- item\n") == "none"


def test_project_row_status_g_and_r() -> None:
    g_status = project_row_status(G_TABLE)
    assert g_status == {"G1": "DONE", "G2": "OPEN"}
    r_status = project_row_status(R_TABLE)
    assert r_status == {"R1": "DONE", "R2": "OPEN"}


def test_ordered_row_ids_and_fold_lines() -> None:
    assert ordered_row_ids(G_TABLE) == ("G1", "G2")
    lines = fold_row_lines(G_TABLE)
    assert len(lines) == 2
    assert lines[0].startswith("| G1 |")
