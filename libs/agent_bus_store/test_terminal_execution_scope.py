"""Sibling failure must not stamp a live link on the same thread (a:36644)."""

from __future__ import annotations

import pytest

from agent_bus_store.db import (
    admit_dispatch,
    create_thread_with_turn,
    get_thread_with_links,
    init_db,
    terminate_dispatch,
)
from agent_bus_store.db.turns import insert_turn
from agent_bus_store.reconcile import reconcile_orphaned_dispatches
from agent_bus_store.sdk_liveness import LivenessVerdict, ProbeResult, classify_probe


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    return db_path


def test_sibling_failure_leaves_live_link_in_flight(bus_db) -> None:
    del bus_db
    thread_row, *_ = create_thread_with_turn(
        slug="two-links",
        from_agent="dispatch",
        to_agent="cursor-sdk",
        subject="cursor-sdk generate",
        body="pointer",
        lifecycle_state="active",
    )
    thread_id = thread_row["id"]
    live = "1843d275-90ac-498b-9418-2d53d2dc56b1"
    sibling = "6db305b2-exec"
    admit_dispatch(
        thread_id=thread_id,
        execution_id=live,
        pipeline_id="cursor-sdk-generate",
        caller_agent="cursor",
    )
    admit_dispatch(
        thread_id=thread_id,
        execution_id=sibling,
        pipeline_id="cursor-sdk-generate",
        caller_agent="cursor",
    )
    terminate_dispatch(
        thread_id=thread_id,
        terminal_status="failed",
        execution_id=sibling,
    )
    insert_turn(
        thread=thread_id,
        from_agent="cursor-sdk",
        to_agent="dispatch",
        subject="cursor-sdk dispatch 6db305b2 FAILED",
        body="sibling bridge abort",
    )

    def _sibling_probe(_thread_id: str) -> ProbeResult:
        return ProbeResult(
            payload={"status": "failed", "execution_id": sibling},
            http_status=200,
            error=None,
        )

    from agent_bus_store import reconcile as reconcile_mod

    original = reconcile_mod.evaluate_link_liveness

    def _scoped(**kwargs: object):
        kwargs["probe_fn"] = _sibling_probe
        return original(**kwargs)  # type: ignore[arg-type]

    reconcile_mod.evaluate_link_liveness = _scoped  # type: ignore[assignment]
    try:
        reconcile_orphaned_dispatches()
    finally:
        reconcile_mod.evaluate_link_liveness = original

    detail = get_thread_with_links(thread_id)
    assert detail is not None
    by_exec = {row["execution_id"]: row for row in detail["dispatch_links"]}
    assert by_exec[sibling]["terminal_status"] == "failed"
    assert by_exec[live]["terminal_status"] is None


def test_omitted_execution_id_does_not_stamp_sibling(bus_db) -> None:
    del bus_db
    thread_row, *_ = create_thread_with_turn(
        slug="omit-exec",
        from_agent="dispatch",
        to_agent="cursor",
        subject="handoff",
        body="brief",
        lifecycle_state="active",
    )
    thread_id = thread_row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id="live-exec",
        pipeline_id="cursor-sdk-generate",
    )
    admit_dispatch(
        thread_id=thread_id,
        execution_id="sib-exec",
        pipeline_id="cursor-sdk-generate",
    )
    terminate_dispatch(thread_id=thread_id, terminal_status="failed")
    detail = get_thread_with_links(thread_id)
    assert detail is not None
    assert all(row["terminal_status"] is None for row in detail["dispatch_links"])


def test_probe_other_execution_does_not_backfill() -> None:
    probe = ProbeResult(
        payload={"status": "failed", "execution_id": "sibling-exec"},
        http_status=200,
        error=None,
    )
    verdict, reason, terminal = classify_probe(
        probe,
        link_execution_id="live-exec",
        sole_link=False,
    )
    assert verdict is LivenessVerdict.SKIP_LIVE
    assert reason == "probe_other_execution"
    assert terminal is None
