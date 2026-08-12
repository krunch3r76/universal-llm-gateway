"""Unit matrix for ``composed_commission`` state ledger (rows 1–10)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from services.git_integration_worker.cursor_auto.composed_commission import (
    COMPOSED_COMMISSION_COMPLETE,
    COMPOSED_COMMISSION_FAILED,
    COMPOSED_COMMISSION_INCOMPLETE,
    COMPOSED_COMMISSION_NA,
    compute_composed_commission,
    prose_composed_commission_line,
    resolve_composition_parent_id,
)


def _ledger(*, children: list[str], statuses: dict[str, str | None]) -> MagicMock:
    ledger = MagicMock()
    ledger.list_nested_children.return_value = children
    ledger.dispatch_status_by_id.side_effect = (
        lambda *, dispatch_id: (
            {"dispatch_id": dispatch_id, "status": statuses[dispatch_id]}
            if statuses.get(dispatch_id) is not None
            else None
        )
    )
    return ledger


def test_resolve_composition_parent_id_prefers_nest_under() -> None:
    assert (
        resolve_composition_parent_id(
            closing_dispatch_id="child-d",
            nest_under="parent-p",
        )
        == "parent-p"
    )
    assert (
        resolve_composition_parent_id(
            closing_dispatch_id="solo-d",
            nest_under=None,
        )
        == "solo-d"
    )


def test_prose_composed_commission_line_format() -> None:
    assert (
        prose_composed_commission_line(COMPOSED_COMMISSION_NA)
        == "composed_commission: n/a — not-in-closure"
    )


@pytest.mark.parametrize(
    ("row", "children", "statuses", "expected"),
    [
        # 1 — empty children
        (1, [], {}, COMPOSED_COMMISSION_NA),
        # 2 — no nest closure, zero children (same N/A literal)
        (2, [], {}, COMPOSED_COMMISSION_NA),
        # 3 — any failed
        (
            3,
            ["c1"],
            {"c1": "failed"},
            COMPOSED_COMMISSION_FAILED,
        ),
        # 4 — cancelled maps to failed
        (
            4,
            ["c1"],
            {"c1": "cancelled"},
            COMPOSED_COMMISSION_FAILED,
        ),
        # 5 — non-terminal children
        (
            5,
            ["c1", "c2"],
            {"c1": "completed", "c2": "running"},
            COMPOSED_COMMISSION_INCOMPLETE,
        ),
        (
            5,
            ["c1"],
            {"c1": "queued"},
            COMPOSED_COMMISSION_INCOMPLETE,
        ),
        (
            5,
            ["c1"],
            {"c1": "admitted"},
            COMPOSED_COMMISSION_INCOMPLETE,
        ),
        (
            5,
            ["c1"],
            {"c1": "parked_waiting"},
            COMPOSED_COMMISSION_INCOMPLETE,
        ),
        # 6 — unknown / unobserved child status
        (
            6,
            ["c1"],
            {"c1": None},
            COMPOSED_COMMISSION_INCOMPLETE,
        ),
        (
            6,
            ["c1"],
            {"c1": "mystery-status"},
            COMPOSED_COMMISSION_INCOMPLETE,
        ),
        # 7 — all completed
        (
            7,
            ["c1", "c2"],
            {"c1": "completed", "c2": "completed"},
            COMPOSED_COMMISSION_COMPLETE,
        ),
        # 8 — failed beats incomplete (mixed)
        (
            8,
            ["c1", "c2"],
            {"c1": "failed", "c2": "running"},
            COMPOSED_COMMISSION_FAILED,
        ),
    ],
)
def test_state_ledger_rows(
    row: int,
    children: list[str],
    statuses: dict[str, str | None],
    expected: str,
) -> None:
    ledger = _ledger(children=children, statuses=statuses)
    assert (
        compute_composed_commission(parent_dispatch_id="parent-p", ledger=ledger)
        == expected
    ), f"state ledger row {row}"


def test_state_ledger_row_9_ledger_list_raises() -> None:
    ledger = MagicMock()
    ledger.list_nested_children.side_effect = RuntimeError("ledger down")
    assert (
        compute_composed_commission(parent_dispatch_id="p", ledger=ledger)
        == COMPOSED_COMMISSION_INCOMPLETE
    )


def test_state_ledger_row_9_ledger_status_raises() -> None:
    ledger = MagicMock()
    ledger.list_nested_children.return_value = ["c1"]
    ledger.dispatch_status_by_id.side_effect = RuntimeError("lookup failed")
    assert (
        compute_composed_commission(parent_dispatch_id="p", ledger=ledger)
        == COMPOSED_COMMISSION_INCOMPLETE
    )


def test_state_ledger_row_10_grandchild_non_rollup() -> None:
    """Grandchild under child must not affect parent's aggregation."""
    ledger = MagicMock()
    # Parent P has only immediate child C1; grandchild G nests under C1.
    ledger.list_nested_children.side_effect = (
        lambda *, parent_dispatch_id: (
            ["c1"] if parent_dispatch_id == "parent-p" else []
        )
    )
    ledger.dispatch_status_by_id.side_effect = (
        lambda *, dispatch_id: {"dispatch_id": dispatch_id, "status": "completed"}
    )
    assert (
        compute_composed_commission(parent_dispatch_id="parent-p", ledger=ledger)
        == COMPOSED_COMMISSION_COMPLETE
    )
    ledger.list_nested_children.assert_called_with(parent_dispatch_id="parent-p")


def test_state_ledger_row_11_nest_under_parent_key() -> None:
    """Child Auto closeout aggregates under nest_under=P when P has children."""
    parent_id = resolve_composition_parent_id(
        closing_dispatch_id="child-closeout",
        nest_under="parent-p",
    )
    ledger = _ledger(
        children=["c1"],
        statuses={"c1": "failed"},
    )
    assert parent_id == "parent-p"
    assert (
        compute_composed_commission(parent_dispatch_id=parent_id, ledger=ledger)
        == COMPOSED_COMMISSION_FAILED
    )
    ledger.list_nested_children.assert_called_with(parent_dispatch_id="parent-p")
