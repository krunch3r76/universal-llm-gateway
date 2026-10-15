"""Wake cursor, CAS scan, claim TTL, and /turns/since shape."""

from __future__ import annotations

import threading

import pytest
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.turns import get_turns
from agent_bus_store.db.connection import connect
from agent_bus_store.db.endpoint_wake_ledger import (
    WakeConflictError,
    advance_scan,
    claim_due_wakes,
    fold_expired_claims,
    get_cursor,
)
from agent_bus_store.server import create_app
from fastapi.testclient import TestClient


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def _turn(thread: str, n_from: str = "cursor", to: str = "grok-bot-gc1") -> dict:
    turn_id, _ts, number, _claimed = insert_turn(
        thread=thread, from_agent=n_from, to_agent=to, subject="FACT|x", body="b"
    )
    return {"agent": to, "thread_id": thread, "turn_id": turn_id, "turn_number": number}


def test_compact_fetch_includes_attestation(db: TestClient) -> None:
    detail = create_thread(thread_id=None, slug="compact-attest")
    assert detail is not None
    thread = str(detail["id"])
    insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="FACT|x",
        body="hidden",
        attested_by="static:cursor@code via=io(127.0.0.1) client=cursor-ide",
        user_agent="Mozilla/5.0",
    )
    rows = get_turns(thread=thread, compact=True)
    assert len(rows) == 1
    assert rows[0]["body"] is None
    assert "via=io(127.0.0.1)" in rows[0]["attested_by"]
    assert rows[0]["user_agent"] == "Mozilla/5.0"


def test_cold_start_cursor_is_max_id(db: TestClient) -> None:
    detail = create_thread(thread_id=None, slug="cold")
    assert detail is not None
    made = _turn(str(detail["id"]))
    cursor = get_cursor()
    assert cursor["high_water_turn_id"] == made["turn_id"]
    assert db.get("/turns/since", params={"after_id": made["turn_id"], "limit": 2}).json()["turns"] == []


def test_turns_since_shape_and_stale_scan(db: TestClient) -> None:
    detail = create_thread(thread_id=None, slug="since")
    assert detail is not None
    thread = str(detail["id"])
    first = _turn(thread)
    second = _turn(thread)
    with connect() as conn:
        conn.execute("DELETE FROM endpoint_wake_cursor")
    get_cursor(now="2020-01-01 00:00:00")
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    resp = db.get("/turns/since", params={"after_id": first["turn_id"] - 1, "limit": 2})
    rows = resp.json()["turns"]
    assert [r["id"] for r in rows] == [first["turn_id"], second["turn_id"]]
    assert "body" not in rows[0]
    spec = db.app.openapi()["paths"]["/turns/since"]["get"]
    assert "x-mcp" not in spec
    advance_scan("endpoint-wake-dispatcher", 0, first["turn_id"], [first])
    with pytest.raises(WakeConflictError):
        advance_scan("endpoint-wake-dispatcher", 0, second["turn_id"], [second])
    with connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM endpoint_wake_ledger").fetchone()["n"]
    assert count == 1


def test_claim_once_and_ttl_folds_unknown(db: TestClient) -> None:
    detail = create_thread(thread_id=None, slug="claim")
    assert detail is not None
    item = _turn(str(detail["id"]))
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan("endpoint-wake-dispatcher", 0, item["turn_id"], [item], now="2020-01-01 00:00:00")
    barrier = threading.Barrier(2)
    results: list[str | None] = []

    def _claim() -> None:
        barrier.wait()
        claimed = claim_due_wakes(now="2020-01-01 00:00:01")
        results.append(claimed["wake_id"])

    threads = [threading.Thread(target=_claim) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for w in results if w) == 1
    wake = next(w for w in results if w)
    assert fold_expired_claims(claim_ttl_s=300, now="2020-01-01 00:06:00") == 1
    again = claim_due_wakes(now="2020-01-01 00:06:01")
    assert again["wake_id"] is None
    with connect() as conn:
        state = conn.execute(
            "SELECT state FROM endpoint_wake_ledger WHERE wake_id=?", (wake,)
        ).fetchone()["state"]
    assert state == "unknown"
