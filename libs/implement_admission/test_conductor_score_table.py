"""Header-located scoreboard table geometry (B0 — a:33504)."""

from __future__ import annotations

import pytest

from implement_admission.conductor_score_table import (
    DEFAULT_STATUS_INDEX,
    DEFAULT_STOPS_INDEX,
    cell,
    header_indices,
    row_id_in,
    set_cell,
    status_index,
    status_token,
    stops_index,
)
from implement_admission.conductor_witness_types import (
    done_rows_claimed_in_closeout,
    row_status_in_tip,
    stops_block_reason,
)

pytestmark = pytest.mark.offline

_FIVE_COLUMN = "\n".join(
    [
        "## Gated deliverables",
        "",
        "| ID | Deliverable | Mode | Status | Stops |",
        "|---|---|---|---|---|",
        "| G3 | Densify | plan | OPEN | |",
        "| G4 | Skeptic | — | OPEN | ROW_PINNED |",
        "| G5 | Implement | agent | DONE | |",
        "",
        "## Sidecars",
        "",
        "| ID | Artifact URI | What it is |",
        "|---|---|---|",
    ]
)
_FOUR_COLUMN = "\n".join(
    [
        "## Gated deliverables",
        "",
        "| ID | Deliverable | Status | Stops |",
        "|---|---|---|---|",
        "| G4 | Skeptic | DONE | ROW_PINNED |",
    ]
)


def test_status_and_stops_located_from_five_column_header() -> None:
    assert status_index(_FIVE_COLUMN) == 4
    assert stops_index(_FIVE_COLUMN) == 5


def test_status_and_stops_located_from_four_column_header() -> None:
    assert status_index(_FOUR_COLUMN) == DEFAULT_STATUS_INDEX
    assert stops_index(_FOUR_COLUMN) == DEFAULT_STOPS_INDEX


def test_headerless_prose_keeps_four_column_positions() -> None:
    """Closeout prose carries rows without a header; the old positions still apply."""
    body = "| G2 | Frame | DONE | |"
    assert header_indices(body) == {}
    assert status_index(body) == DEFAULT_STATUS_INDEX


def test_sidecar_header_is_not_mistaken_for_the_gated_table() -> None:
    body = "| ID | Artifact URI | What it is |\n|---|---|---|\n" + _FIVE_COLUMN
    assert status_index(body) == 4


def test_row_id_only_matches_scoreboard_rows() -> None:
    assert row_id_in("| G4 | Skeptic | — | OPEN | |") == "G4"
    assert row_id_in("| r2 | Finding | OPEN | |") == "R2"
    assert row_id_in("| ID | Deliverable | Status | Stops |") is None
    assert row_id_in("|---|---|---|---|") is None
    assert row_id_in("prose about G4") is None


def test_set_cell_preserves_neighbouring_cells() -> None:
    line = "| G5 | Implement | agent | OPEN | |"
    assert set_cell(line, 4, "DONE") == "| G5 | Implement | agent | DONE | |"
    assert cell(set_cell(line, 4, "DONE"), 3) == "agent"


def test_set_cell_leaves_short_rows_alone() -> None:
    assert set_cell("| G5 |", 4, "DONE") == "| G5 |"


def test_status_token_reads_qualified_and_trailing_prose() -> None:
    assert status_token("WIP(conductor)") == "WIP(CONDUCTOR)"
    assert status_token("DONE — landed") == "DONE"
    assert status_token("—") is None


def test_row_status_reads_status_not_mode() -> None:
    """HEAD read cell 3, so G3 reported its Mode (``plan``) as a status."""
    assert row_status_in_tip(_FIVE_COLUMN, "G3") == "OPEN"
    assert row_status_in_tip(_FIVE_COLUMN, "G5") == "DONE"
    assert row_status_in_tip(_FOUR_COLUMN, "G4") == "DONE"


def test_stops_block_reads_stops_not_status() -> None:
    """HEAD read cell 4, which is Status on a five-column board."""
    assert stops_block_reason(_FIVE_COLUMN, "G4") == "ROW_PINNED"
    assert stops_block_reason(_FIVE_COLUMN, "G3") is None
    assert stops_block_reason(_FOUR_COLUMN, "G4") == "ROW_PINNED"


def test_done_claims_follow_the_header() -> None:
    assert done_rows_claimed_in_closeout(_FIVE_COLUMN) == frozenset({"G5"})
    assert done_rows_claimed_in_closeout(_FOUR_COLUMN) == frozenset({"G4"})
