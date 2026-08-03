"""Hermetic tests for seat_claims — presence + exclusive claim with TTL."""

from __future__ import annotations

import importlib.util
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from agent_seat.registry import normalize_bus_address
from cortex_store.seat_claim_store import (
    claim_seat,
    heartbeat_seat,
    list_seat_claims,
    release_seat,
)

_MIG074 = Path(__file__).parent / "migrations" / "074_seat_claims.py"


def _load_migration(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


migration_074 = _load_migration(_MIG074, "migration_074")


def _apply_schema(conn: sqlite3.Connection) -> None:
    migration_074.migrate(conn)
    conn.commit()


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    _apply_schema(c)
    return c


@pytest.fixture()
def conn_path(tmp_path: Path) -> Path:
    db_path = tmp_path / "seat_claims.db"
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    _apply_schema(c)
    c.close()
    return db_path


@pytest.mark.offline
def test_exclusion_regression_6655(conn: sqlite3.Connection) -> None:
    """Two seats contending for one key — second claimant loses at claim time."""
    a = claim_seat(conn, claim_key="operator-proxy", seat="web-anthropic")
    assert a["granted"] is True

    b = claim_seat(conn, claim_key="operator-proxy", seat="cursor")
    assert b["granted"] is False
    assert b["holder"]["seat"] == "web-anthropic"
    assert b["holder"]["holder_id"] == a["holder_id"]


@pytest.mark.offline
def test_concurrency_exactly_one_granted(conn_path: Path) -> None:
    results: list[dict] = []
    barrier = threading.Barrier(2)

    def _worker(seat: str) -> None:
        worker_conn = sqlite3.connect(conn_path)
        worker_conn.row_factory = sqlite3.Row
        barrier.wait()
        results.append(
            claim_seat(worker_conn, claim_key="race-key", seat=seat)
        )
        worker_conn.close()

    t1 = threading.Thread(target=_worker, args=("seat-a",))
    t2 = threading.Thread(target=_worker, args=("seat-b",))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    granted = [r for r in results if r.get("granted")]
    assert len(results) == 2
    assert len(granted) == 1


@pytest.mark.offline
def test_ttl_reclaim_stale_end_reason(conn: sqlite3.Connection) -> None:
    a = claim_seat(conn, claim_key="k", seat="cursor", ttl_s=0.1)
    assert a["granted"] is True
    time.sleep(0.15)

    b = claim_seat(conn, claim_key="k", seat="web-anthropic")
    assert b["granted"] is True

    ended = conn.execute(
        "SELECT status, end_reason FROM seat_claims WHERE holder_id=?",
        (a["holder_id"],),
    ).fetchone()
    assert ended["status"] == "reclaimed"
    assert ended["end_reason"] == "stale"


@pytest.mark.offline
def test_arming_cap_no_heartbeat(conn: sqlite3.Connection) -> None:
    a = claim_seat(conn, claim_key="k", seat="cursor", ttl_s=0.1)
    assert a["granted"] is True
    time.sleep(0.15)

    b = claim_seat(conn, claim_key="k", seat="web-anthropic")
    assert b["granted"] is True

    row = conn.execute(
        "SELECT status, end_reason, last_heartbeat_at FROM seat_claims WHERE holder_id=?",
        (a["holder_id"],),
    ).fetchone()
    assert row["last_heartbeat_at"] is None
    assert row["status"] == "reclaimed"
    assert row["end_reason"] == "stale"


@pytest.mark.offline
def test_heartbeat_defeats_reclaim(conn: sqlite3.Connection) -> None:
    a = claim_seat(conn, claim_key="k", seat="cursor", ttl_s=0.5)
    assert heartbeat_seat(conn, holder_id=a["holder_id"])["ok"] is True
    time.sleep(0.2)
    assert heartbeat_seat(conn, holder_id=a["holder_id"])["ok"] is True
    time.sleep(0.2)
    assert heartbeat_seat(conn, holder_id=a["holder_id"])["ok"] is True

    b = claim_seat(conn, claim_key="k", seat="web-anthropic")
    assert b["granted"] is False


@pytest.mark.offline
def test_stale_holder_cannot_resurrect(conn: sqlite3.Connection) -> None:
    a = claim_seat(conn, claim_key="k", seat="cursor", ttl_s=0.1)
    time.sleep(0.15)
    b = claim_seat(conn, claim_key="k", seat="web-anthropic")
    assert b["granted"] is True

    hb = heartbeat_seat(conn, holder_id=a["holder_id"])
    assert hb["ok"] is False

    held = conn.execute(
        "SELECT holder_id FROM seat_claims WHERE claim_key='k' AND status='held'"
    ).fetchone()
    assert held["holder_id"] == b["holder_id"]


@pytest.mark.offline
def test_canonical_seat_claude_cursor(conn: sqlite3.Connection) -> None:
    claim_seat(conn, claim_key="k", seat="claude-cursor")
    listed = list_seat_claims(conn, seat="cursor")
    assert len(listed["claims"]) == 1
    assert listed["claims"][0]["seat"] == normalize_bus_address("claude-cursor")


@pytest.mark.offline
def test_clean_release_distinguishable(conn: sqlite3.Connection) -> None:
    a = claim_seat(conn, claim_key="k", seat="cursor")
    released = release_seat(conn, holder_id=a["holder_id"])
    assert released == {"released": True, "end_reason": "released"}

    row = conn.execute(
        "SELECT end_reason FROM seat_claims WHERE holder_id=?",
        (a["holder_id"],),
    ).fetchone()
    assert row["end_reason"] == "released"
    assert row["end_reason"] != "stale"


@pytest.mark.offline
def test_idempotent_self_reclaim(conn: sqlite3.Connection) -> None:
    first = claim_seat(conn, claim_key="k", seat="cursor")
    second = claim_seat(conn, claim_key="k", seat="cursor")
    assert second["granted"] is True
    assert second["holder_id"] == first["holder_id"]
    assert second["claimed_at"] == first["claimed_at"]

    count = conn.execute(
        "SELECT COUNT(*) AS n FROM seat_claims WHERE claim_key='k' AND status='held'"
    ).fetchone()["n"]
    assert count == 1


@pytest.mark.offline
def test_partial_index_blocks_second_held(conn: sqlite3.Connection) -> None:
    claim_seat(conn, claim_key="k", seat="cursor")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO seat_claims "
            "(claim_key, seat, holder_id, status, claimed_at, ttl_s) "
            "VALUES ('k', 'web-anthropic', 'fake-holder', 'held', "
            "'2026-01-01T00:00:00Z', 300.0)"
        )
