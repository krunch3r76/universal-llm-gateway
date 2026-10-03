"""Master land lease reaper — holder liveness, not fixed age (a:37725)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    _connect,
)
from services.git_integration_worker import cursor_sdk_land_lease as land_lease_mod
from services.git_integration_worker.cursor_sdk_land_lease import (
    ensure_land_lease_schema,
    land_lease_holder,
    reap_stale_land_leases,
    refresh_land_lease_heartbeat,
    release_land_lease,
    try_acquire_land_lease,
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
    assert try_acquire_land_lease(lease_key=lease_key, holder_op_id="op-live")
    with _connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_land_leases SET acquired_at=? WHERE lease_key=?",
            (_iso_ago(seconds=1000.0), lease_key),
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


def test_same_op_reacquire_refreshes_dead_pid() -> None:
    lease_key = "/tmp/reap-same-op-dead-pid"
    _insert_lease(
        lease_key=lease_key,
        holder_op_id="op-resume",
        acquired_at=_iso_ago(seconds=1000.0),
        holder_pid=2_000_000_001,
    )
    assert try_acquire_land_lease(lease_key=lease_key, holder_op_id="op-resume")
    with _connect() as conn:
        row = conn.execute(
            "SELECT holder_pid FROM cursor_sdk_land_leases WHERE lease_key=?",
            (lease_key,),
        ).fetchone()
    assert row is not None
    assert int(row["holder_pid"]) == os.getpid()
    assert reap_stale_land_leases() == 0


def test_reap_conditional_delete_survives_mid_loop_reacquire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease_key = "/tmp/reap-race"
    _insert_lease(
        lease_key=lease_key,
        holder_op_id="op-a",
        acquired_at=_iso_ago(seconds=1000.0),
        holder_pid=2_000_000_002,
    )
    real_alive = land_lease_mod._holder_pid_alive
    swapped = {"done": False}

    def _alive_swap_mid_reap(pid: int | None) -> bool | None:
        result = real_alive(pid)
        if result is False and not swapped["done"]:
            swapped["done"] = True
            assert release_land_lease(lease_key=lease_key, holder_op_id="op-a")
            assert try_acquire_land_lease(lease_key=lease_key, holder_op_id="op-b")
        return result

    monkeypatch.setattr(land_lease_mod, "_holder_pid_alive", _alive_swap_mid_reap)
    assert reap_stale_land_leases() == 0
    assert land_lease_holder(lease_key) == "op-b"


def test_legacy_schema_migration_idempotent() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE cursor_sdk_land_leases (
                lease_key TEXT PRIMARY KEY,
                holder_op_id TEXT NOT NULL,
                acquired_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO cursor_sdk_land_leases VALUES (?, ?, ?)",
            ("/tmp/legacy", "legacy-op", _iso_ago(seconds=10.0)),
        )
        ensure_land_lease_schema(conn)
        cols = {
            row[1] for row in conn.execute("PRAGMA table_info(cursor_sdk_land_leases)")
        }
        assert "holder_pid" in cols
        ensure_land_lease_schema(conn)


def test_null_pid_row_between_900s_and_backstop_not_reaped() -> None:
    lease_key = "/tmp/reap-null-pid-mid-age"
    _insert_lease(
        lease_key=lease_key,
        holder_op_id="op-null-mid",
        acquired_at=_iso_ago(seconds=1000.0),
        holder_pid=None,
    )
    assert reap_stale_land_leases(backstop_s=7200.0) == 0
    assert land_lease_holder(lease_key) == "op-null-mid"


def test_try_acquire_records_holder_pid() -> None:
    lease_key = "/tmp/reap-acquire-pid"
    assert try_acquire_land_lease(lease_key=lease_key, holder_op_id="op-pid")
    with _connect() as conn:
        row = conn.execute(
            "SELECT holder_pid FROM cursor_sdk_land_leases WHERE lease_key=?",
            (lease_key,),
        ).fetchone()
    assert row is not None
    assert int(row["holder_pid"]) == os.getpid()


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
