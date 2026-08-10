"""A hop that yields no joinable execution id must still take the cooldown.

Recording a succession claim with ``execution_id: null`` makes the claim
unjoinable, so ``stall_matches_claim`` discards every ``cdp.generate.stalled``
and the revoke breaker never counts (observed 2026-08-09: 16 live watches all
``pending`` with a null id, 127 stalls, 0 revocations).

Declining to record the claim is correct, but skipping ``mark_hop_fired``
outright leaves ``last_hop_at`` unadvanced, so ``evaluate_watch`` re-fires on
the next scan — a ~30s hot loop in place of the 30m cadence, precisely when the
substrate is already failing every generate.
"""

from __future__ import annotations

from pathlib import Path

from services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile import (
    REVOKE_BREAKER_N,
    breaker_blocks_hop,
)
from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
    evaluate_watch,
    load_watches,
    mark_hop_failed,
    mark_hop_fired,
)


def _watches_file(tmp_path: Path) -> Path:
    return tmp_path / "hop_cadence_watches.json"


def test_failed_hop_takes_cooldown_instead_of_refiring(tmp_path: Path) -> None:
    path = _watches_file(tmp_path)
    now = 1_000_000.0
    mark_hop_failed("7046", reason="missing_execution_id", now=now, path=path)

    row = load_watches(path)["7046"]
    assert row["last_hop_at"] == now, "cooldown clock not advanced"

    # One scan interval later the lane must still be cooling down, not firing.
    decision = evaluate_watch(row, now=now + 30.0, threshold=1500.0, cool=1800.0)
    assert decision.action == "skip"
    assert decision.reason == "cooldown"


def test_failed_hop_records_no_unjoinable_claim(tmp_path: Path) -> None:
    path = _watches_file(tmp_path)
    mark_hop_failed("7046", reason="missing_execution_id", now=1_000_000.0, path=path)

    row = load_watches(path)["7046"]
    assert "pending_succession" not in row
    assert row.get("succession_status") != "pending"


def test_repeated_unjoinable_hops_trip_the_breaker(tmp_path: Path) -> None:
    path = _watches_file(tmp_path)
    now = 1_000_000.0
    for i in range(REVOKE_BREAKER_N):
        mark_hop_failed(
            "7046", reason="missing_execution_id", now=now + i * 1801.0, path=path
        )

    row = load_watches(path)["7046"]
    assert row["consecutive_hop_failures"] == REVOKE_BREAKER_N
    assert breaker_blocks_hop(row), "breaker never trips on unjoinable hops"


def test_successful_hop_clears_the_failure_streak(tmp_path: Path) -> None:
    path = _watches_file(tmp_path)
    now = 1_000_000.0
    mark_hop_failed("7046", reason="missing_execution_id", now=now, path=path)
    mark_hop_fired("7046", now=now + 1.0, path=path, execution_id="exec-1")

    row = load_watches(path)["7046"]
    assert "consecutive_hop_failures" not in row
    assert not breaker_blocks_hop(row)
