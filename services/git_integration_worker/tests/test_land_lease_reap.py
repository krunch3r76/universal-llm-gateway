"""Master land lease reaper — holder liveness, not fixed age (a:37725)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    _connect,
)
from services.git_integration_worker.cursor_sdk_land_lease import (
    ensure_land_lease_schema,
    land_lease_holder,
    reap_stale_land_leases,
    refresh_land_lease_heartbeat,
)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _insert_lease(
    *,
    lease_key: str,
    holder_op_id: str,
    acquired_at: str,
    holder_pid: int | None,
) -> None:
    with _connect() as conn:
        ensure_land_lease_schema(conn)
        conn.execute(
            "INSERT INTO cursor_sdk_land_leases "
            "(lease_key, holder_op_id, acquired_at, holder_pid) "
            "VALUES (?, ?, ?, ?)",
            (lease_key, holder_op_id, acquired_at, holder_pid),
        )


def _iso_ago(*, seconds: float) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


def test_reap_skips_live_holder_older_than_900s() -> None:
    """Live holder pid must survive long suite gates (>900s)."""
    lease_key = "/tmp/reap-live-holder"
    _insert_lease(
        lease_key=lease_key,
        holder_op_id="op-live",
        acquired_at=_iso_ago(seconds=1000.0),
        holder_pid=os.getpid(),
    )
    assert reap_stale_land_leases() == 0
    assert land_lease_holder(lease_key) == "op-live"


def test_reap_deletes_dead_holder_regardless_of_age() -> None:
    lease_key = "/tmp/reap-dead-holder"
    _insert_lease(
        lease_key=lease_key,
        holder_op_id="op-dead",
        acquired_at=_iso_ago(seconds=10.0),
        holder_pid=2_000_000_000,
    )
    assert reap_stale_land_leases() == 1
    assert land_lease_holder(lease_key) is None


def test_reap_uncheckable_holder_after_backstop() -> None:
    lease_key = "/tmp/reap-no-pid-backstop"
    _insert_lease(
        lease_key=lease_key,
        holder_op_id="op-no-pid",
        acquired_at=_iso_ago(seconds=7300.0),
        holder_pid=None,
    )
    assert reap_stale_land_leases(backstop_s=7200.0) == 1
    assert land_lease_holder(lease_key) is None


def test_heartbeat_refreshes_timestamp_for_pidless_holder() -> None:
    lease_key = "/tmp/reap-heartbeat"
    old = _iso_ago(seconds=7300.0)
    _insert_lease(
        lease_key=lease_key,
        holder_op_id="op-heartbeat",
        acquired_at=old,
        holder_pid=None,
    )
    assert refresh_land_lease_heartbeat(
        lease_key=lease_key, holder_op_id="op-heartbeat"
    )
    with _connect() as conn:
        row = conn.execute(
            "SELECT acquired_at FROM cursor_sdk_land_leases WHERE lease_key=?",
            (lease_key,),
        ).fetchone()
    assert row is not None
    assert row["acquired_at"] != old
    assert reap_stale_land_leases(backstop_s=7200.0) == 0
