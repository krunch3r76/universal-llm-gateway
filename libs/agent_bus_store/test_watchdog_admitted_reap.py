"""Admitted-TTL reap must not close a thread whose GIW holder is live.

Specimen (thread 9476, 2026-08-18): bus_lifecycle_state=admitted, turn_count=1,
updated_at older than AGENT_BUS_WATCHDOG_ADMITTED_TTL, holder still live
(running or parked_waiting). Pre-fix _reap_admitted closed on the bus clock
alone. Post-fix probes the holder and fail-closes on SKIP_LIVE / DEFER.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from agent_bus_store.db import (
    admit_dispatch,
    create_thread_with_turn,
    get_thread,
    init_db,
)
from agent_bus_store.db.connection import connect
from agent_bus_store.sdk_liveness import ProbeResult
from agent_bus_store.watchdog import _cutoff_for_ttl, _reap_admitted, _sweep

pytestmark = pytest.mark.offline


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    return db_path


def _admit_quiet_thread(
    *,
    slug: str,
    execution_id: str,
    age_s: int = 4000,
) -> str:
    """Pending pointer + admit, then age updated_at past the default 3600s TTL."""
    thread_row, *_ = create_thread_with_turn(
        slug=slug,
        from_agent="dispatch",
        to_agent="claude-cursor",
        subject="cursor-sdk generate",
        body="pointer only",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    admit_dispatch(
        thread_id=thread_id,
        execution_id=execution_id,
        pipeline_id="cursor-sdk-generate",
        caller_agent="claude-web",
    )
    old = (datetime.now(UTC) - timedelta(seconds=age_s)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with connect() as conn:
        conn.execute(
            "UPDATE threads SET updated_at = ? WHERE id = ?",
            (old, thread_id),
        )
    detail = get_thread(thread_id)
    assert detail is not None
    assert detail["bus_lifecycle_state"] == "admitted"
    assert int(detail["turn_count"]) == 1
    return thread_id


def _probe(*, status: str, execution_id: str, heartbeat: str | None):
    def _fn(_thread_id: str) -> ProbeResult:
        payload: dict[str, object] = {"status": status, "execution_id": execution_id}
        if heartbeat is not None:
            payload["last_heartbeat_at"] = heartbeat
        return ProbeResult(payload=payload, http_status=200, error=None)

    return _fn


def _fresh() -> str:
    return datetime.now(UTC).isoformat()


def _stale() -> str:
    return (datetime.now(UTC) - timedelta(seconds=400)).isoformat()


def _lifecycle(thread_id: str) -> tuple[str, str]:
    detail = get_thread(thread_id)
    assert detail is not None
    return detail["bus_lifecycle_state"], detail["status"]


def test_admitted_ttl_live_holder_survives(bus_db) -> None:
    """Today's specimen: admitted, turn_count=1, bus clock expired, holder live."""
    thread_id = _admit_quiet_thread(slug="ttl-live-running", execution_id="exec-live")
    with patch("agent_bus_store.watchdog.emit_thread_abandoned"):
        _reap_admitted(
            _cutoff_for_ttl(3600),
            probe_fn=_probe(
                status="running", execution_id="exec-live", heartbeat=_fresh()
            ),
        )

    lifecycle, status = _lifecycle(thread_id)
    assert lifecycle == "admitted"
    assert status != "closed"


def test_admitted_ttl_parked_waiting_holder_survives(bus_db) -> None:
    """AC2: parked_waiting is live even when parent heartbeat is stale."""
    thread_id = _admit_quiet_thread(slug="ttl-live-parked", execution_id="exec-parked")
    with patch("agent_bus_store.watchdog.emit_thread_abandoned"):
        _reap_admitted(
            _cutoff_for_ttl(3600),
            probe_fn=_probe(
                status="parked_waiting",
                execution_id="exec-parked",
                heartbeat=_stale(),
            ),
        )

    lifecycle, status = _lifecycle(thread_id)
    assert lifecycle == "admitted"
    assert status != "closed"


def test_admitted_ttl_defer_fail_closed(bus_db) -> None:
    """Unreachable GIW must not reap (fail-closed on DEFER)."""
    thread_id = _admit_quiet_thread(slug="ttl-defer", execution_id="exec-defer")

    def _unreachable(_thread_id: str) -> ProbeResult:
        return ProbeResult(
            payload=None, http_status=None, error="probe_unreachable:timed out"
        )

    with patch("agent_bus_store.watchdog.emit_thread_abandoned"):
        _reap_admitted(_cutoff_for_ttl(3600), probe_fn=_unreachable)

    lifecycle, _status = _lifecycle(thread_id)
    assert lifecycle == "admitted"


def test_admitted_ttl_dead_holder_still_reaped(bus_db) -> None:
    """True death (no GIW row) still abandons after TTL."""
    thread_id = _admit_quiet_thread(slug="ttl-dead", execution_id="exec-dead")

    def _missing(_thread_id: str) -> ProbeResult:
        return ProbeResult(payload=None, http_status=404, error=None)

    with patch("agent_bus_store.watchdog.emit_thread_abandoned") as abandoned:
        _reap_admitted(_cutoff_for_ttl(3600), probe_fn=_missing)

    lifecycle, status = _lifecycle(thread_id)
    assert lifecycle == "abandoned"
    assert status == "closed"
    abandoned.assert_called_once()
    assert abandoned.call_args.kwargs["reason"] == "admitted_ttl_exceeded"


def test_admitted_ttl_sqlite_format_updated_at_still_reaped(bus_db) -> None:
    """Mixed on-disk timestamps: sqlite datetime() form must still select."""
    thread_id = _admit_quiet_thread(slug="ttl-sqlite-ts", execution_id="exec-sql")
    old = (datetime.now(UTC) - timedelta(seconds=4000)).strftime("%Y-%m-%d %H:%M:%S")
    with connect() as conn:
        conn.execute(
            "UPDATE threads SET updated_at = ? WHERE id = ?",
            (old, thread_id),
        )

    def _missing(_thread_id: str) -> ProbeResult:
        return ProbeResult(payload=None, http_status=404, error=None)

    with patch("agent_bus_store.watchdog.emit_thread_abandoned"):
        _reap_admitted(_cutoff_for_ttl(3600), probe_fn=_missing)

    lifecycle, status = _lifecycle(thread_id)
    assert lifecycle == "abandoned"
    assert status == "closed"


def test_cutoff_iso_matches_now_format() -> None:
    """Cutoff must compare as older against an ISO updated_at from now()."""
    cutoff = _cutoff_for_ttl(3600)
    assert "T" in cutoff
    assert cutoff.endswith("Z")
    aged = (datetime.now(UTC) - timedelta(seconds=4000)).strftime("%Y-%m-%dT%H:%M:%SZ")
    normalized_aged = aged.replace("T", " ").replace("Z", "")
    normalized_cutoff = cutoff.replace("T", " ").replace("Z", "")
    assert normalized_aged < normalized_cutoff


def test_sweep_reaps_admitted_before_reconcile(bus_db) -> None:
    """AC4: probe-in-reap is the fix; sweep order stays admitted-then-reconcile."""
    order: list[str] = []

    def _pending(_cutoff: str) -> None:
        order.append("pending")

    def _admitted(_cutoff: str) -> None:
        order.append("admitted")

    def _active(_cutoff: str) -> None:
        order.append("active")

    def _reconcile() -> int:
        order.append("reconcile")
        return 0

    def _quiet() -> int:
        order.append("quiet")
        return 0

    with (
        patch("agent_bus_store.watchdog._reap_pending", side_effect=_pending),
        patch("agent_bus_store.watchdog._reap_admitted", side_effect=_admitted),
        patch("agent_bus_store.watchdog._reap_active", side_effect=_active),
        patch(
            "agent_bus_store.reconcile.reconcile_orphaned_dispatches",
            side_effect=_reconcile,
        ),
        patch(
            "agent_bus_store.quiet_sweep.sweep_quiet_with_wip",
            side_effect=_quiet,
        ),
    ):
        _sweep()

    assert order == ["pending", "admitted", "active", "reconcile", "quiet"]
