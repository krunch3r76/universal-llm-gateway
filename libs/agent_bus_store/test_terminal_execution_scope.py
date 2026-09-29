"""Sibling failure must not stamp a live link on the same thread (a:36644)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

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


def _open_thread(slug: str) -> str:
    thread_row, *_ = create_thread_with_turn(
        slug=slug,
        from_agent="dispatch",
        to_agent="cursor-sdk",
        subject="cursor-sdk generate",
        body="pointer",
        lifecycle_state="active",
    )
    return str(thread_row["id"])


def _statuses(thread_id: str) -> dict[str, str | None]:
    detail = get_thread_with_links(thread_id)
    assert detail is not None
    return {row["execution_id"]: row["terminal_status"] for row in detail["dispatch_links"]}


def _iso_ago(delta: timedelta) -> str:
    return (datetime.now(UTC) - delta).isoformat().replace("+00:00", "Z")


def _run_reaper(probe_for: Any) -> None:
    from agent_bus_store import reconcile as reconcile_mod

    original = reconcile_mod.evaluate_link_liveness

    def _scoped(**kwargs: Any) -> Any:
        payload = probe_for(
            str(kwargs["link_execution_id"]),
            str(kwargs["thread_id"]),
        )

        def _probe(_thread_id: str) -> ProbeResult:
            return ProbeResult(payload=payload, http_status=200, error=None)

        kwargs["probe_fn"] = _probe
        return original(**kwargs)

    reconcile_mod.evaluate_link_liveness = _scoped  # type: ignore[assignment]
    try:
        reconcile_orphaned_dispatches()
    finally:
        reconcile_mod.evaluate_link_liveness = original


def test_fan_out_stamps_other_thread_same_execution(bus_db) -> None:
    del bus_db
    thread_a = _open_thread("fan-a")
    thread_b = _open_thread("fan-b")
    execution_e = "exec-fan-e"
    execution_l = "exec-fan-l"
    admit_dispatch(
        thread_id=thread_a,
        execution_id=execution_e,
        pipeline_id="cursor-sdk-generate",
    )
    admit_dispatch(
        thread_id=thread_b,
        execution_id=execution_e,
        pipeline_id="cursor-sdk-generate",
    )
    admit_dispatch(
        thread_id=thread_b,
        execution_id=execution_l,
        pipeline_id="cursor-sdk-generate",
    )
    before = get_thread_with_links(thread_b)
    assert before is not None
    lifecycle_before = before["bus_lifecycle_state"]

    terminate_dispatch(
        thread_id=thread_a,
        terminal_status="completed",
        execution_id=execution_e,
        fan_out=True,
    )

    assert _statuses(thread_a) == {execution_e: "completed"}
    assert _statuses(thread_b) == {execution_e: "completed", execution_l: None}
    after = get_thread_with_links(thread_b)
    assert after is not None
    assert after["bus_lifecycle_state"] == lifecycle_before


def test_case_b_live_40min_stays_null(bus_db) -> None:
    """A 40-minute-old dispatch with a fresh heartbeat is live on every link."""
    del bus_db
    conductor = _open_thread("case-b-conductor")
    second = _open_thread("case-b-second")
    execution_e = "exec-case-b-e"
    execution_l = "exec-case-b-l"
    admit_dispatch(
        thread_id=conductor,
        execution_id=execution_e,
        pipeline_id="cursor-sdk-generate",
    )
    admit_dispatch(
        thread_id=second,
        execution_id=execution_e,
        pipeline_id="cursor-sdk-generate",
    )
    admit_dispatch(
        thread_id=second,
        execution_id=execution_l,
        pipeline_id="cursor-sdk-generate",
    )
    fresh = _iso_ago(timedelta(seconds=20))
    started = _iso_ago(timedelta(minutes=40))

    def _probe(link_execution_id: str, _thread_id: str) -> dict[str, str]:
        if link_execution_id == execution_e:
            return {
                "status": "running",
                "execution_id": execution_e,
                "last_heartbeat_at": fresh,
                "started_at": started,
            }
        return {
            "status": "running",
            "execution_id": execution_l,
            "last_heartbeat_at": fresh,
        }

    _run_reaper(_probe)
    _run_reaper(_probe)
    assert _statuses(conductor) == {execution_e: None}
    assert _statuses(second) == {execution_e: None, execution_l: None}


def test_reaper_does_not_fan_out_mismatched_sole_link(bus_db) -> None:
    del bus_db
    thread_a = _open_thread("mismatch-a")
    thread_b = _open_thread("mismatch-b")
    execution_e = "exec-mismatch-e"
    other = "exec-mismatch-other"
    admit_dispatch(
        thread_id=thread_a,
        execution_id=execution_e,
        pipeline_id="cursor-sdk-generate",
    )
    admit_dispatch(
        thread_id=thread_b,
        execution_id=execution_e,
        pipeline_id="cursor-sdk-generate",
    )
    fresh = _iso_ago(timedelta(seconds=20))

    def _probe(link_execution_id: str, thread_id: str) -> dict[str, str]:
        del link_execution_id
        if thread_id == thread_a:
            return {"status": "failed", "execution_id": other}
        return {
            "status": "running",
            "execution_id": execution_e,
            "last_heartbeat_at": fresh,
        }

    _run_reaper(_probe)
    assert _statuses(thread_a) == {execution_e: "failed"}
    assert _statuses(thread_b) == {execution_e: None}


def test_park_parent_does_not_fan_out(bus_db) -> None:
    """A park_for_restart cancel shares execution_id with the running child."""
    del bus_db
    parent = _open_thread("park-parent")
    child = _open_thread("park-child")
    execution_e = "exec-park-shared"
    admit_dispatch(
        thread_id=parent,
        execution_id=execution_e,
        pipeline_id="cursor-sdk-generate",
    )
    admit_dispatch(
        thread_id=child,
        execution_id=execution_e,
        pipeline_id="cursor-sdk-generate",
    )
    fresh = _iso_ago(timedelta(seconds=20))

    def _probe(_link_execution_id: str, thread_id: str) -> dict[str, Any]:
        if thread_id == parent:
            return {
                "status": "cancelled",
                "execution_id": execution_e,
                "park": {"state": "resumed", "park_kind": "park_for_restart"},
            }
        return {
            "status": "running",
            "execution_id": execution_e,
            "last_heartbeat_at": fresh,
        }

    _run_reaper(_probe)
    assert _statuses(parent) == {execution_e: None}
    assert _statuses(child) == {execution_e: None}

    terminate_dispatch(
        thread_id=child,
        terminal_status="completed",
        execution_id=execution_e,
        fan_out=True,
    )
    assert _statuses(parent) == {execution_e: "completed"}
    assert _statuses(child) == {execution_e: "completed"}
