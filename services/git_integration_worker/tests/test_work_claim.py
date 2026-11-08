"""House claim occupancy: one holder per work_key, expiry, and projection."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from universal_protocol.errors import ProtocolError

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    SourceRefConflict,
    _migrate_claimed_status,
)
from services.git_integration_worker.cursor_sdk_gate import (
    _active_by_lane,
    sdk_busy_status_envelope,
)
from services.git_integration_worker.cursor_sdk_orphan import _bridge_is_owned
from services.git_integration_worker.cursor_sdk_work_claim import (
    ClaimError,
    WorkKeyHeld,
    claim,
    claim_id_for,
    expire_claims,
    registry,
    release,
    update,
)
from services.git_integration_worker.cursor_sdk_worktree_reconcile import (
    _REMIT_SKIP_STATUSES,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)
from services.git_integration_worker.routes.cursor_claim import router as claim_router

pytestmark = pytest.mark.offline

_KEY = "todo:house-state-primitive"
_OLD_DDL = """
CREATE TABLE cursor_sdk_dispatches (
    dispatch_id        TEXT PRIMARY KEY,
    fingerprint        TEXT NOT NULL,
    thread_id          TEXT NOT NULL,
    execution_id       TEXT,
    caller_agent       TEXT,
    resolved_model     TEXT NOT NULL,
    packet_path        TEXT,
    message_present    INTEGER NOT NULL DEFAULT 0,
    state_root         TEXT,
    sdk_agent_id       TEXT,
    sdk_run_id         TEXT,
    status             TEXT NOT NULL CHECK (status IN ('queued','admitted','running','parked_waiting','completed','failed','cancelled')),
    started_at         TEXT,
    last_heartbeat_at  TEXT,
    terminal_status    TEXT CHECK (terminal_status IN ('completed','failed','cancelled')),
    work_fingerprint   TEXT,
    terminal_at        TEXT,
    record_json        TEXT NOT NULL DEFAULT '{}',
    queued_at          TEXT,
    park_child_dispatch_id TEXT
);
CREATE INDEX idx_sdk_dispatch_running
    ON cursor_sdk_dispatches(status) WHERE status = 'running';
CREATE INDEX idx_sdk_dispatch_execution
    ON cursor_sdk_dispatches(execution_id);
"""


def _ledger() -> CursorDispatchLedger:
    return CursorDispatchLedger.instance()


def _req(**overrides: object) -> CursorDispatchRequest:
    base: dict[str, object] = {
        "thread_id": "16006",
        "model": "cursor/grok-4.7",
        "dispatch_id": "disp-house-1",
        "execution_id": "exec-house-1",
        "message": "house admit",
        "read_only": True,
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def test_conductor_direct_admit_empty_generation_options_raises() -> None:
    ledger = _ledger()
    with pytest.raises(ProtocolError) as excinfo:
        _admit(
            ledger,
            dispatch_id="cond-direct",
            work_key="todo:impresario-direct-admit",
            contract="conductor",
            generation_options={},
        )
    assert excinfo.value.code == "IMPRESARIO_ENVELOPE_MISSING"


def test_no_impresario_leases_table_after_claim() -> None:
    ledger = _ledger()
    claim(_KEY, "seat-a", "16006", ledger=ledger)
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ("impresario_leases",),
        ).fetchone()
    assert row is None


def _admit(
    ledger: CursorDispatchLedger,
    *,
    work_key: str = _KEY,
    dispatch_id: str = "disp-house-1",
    thread_id: str = "16006",
    claim_id: str | None = None,
    force: bool = False,
    contract: str = "implement",
    generation_options: dict | None = None,
) -> None:
    req = _req(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        execution_id=f"exec-{dispatch_id}",
        message=f"msg-{dispatch_id}",
        generation_options=generation_options,
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="seat-a",
        resolved_model="grok-4.7",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id="grok-4.7",
        ),
        contract=contract,
        read_only=True,
        work_key=work_key,
        source_ref=work_key,
        identity_class="declared",
        claim_id=claim_id,
        force=force,
    )


def _status(ledger: CursorDispatchLedger, dispatch_id: str) -> str:
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT status, record_json, claim_expires_at FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    assert row is not None
    return str(row["status"])


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(claim_router)
    return TestClient(app)


def test_t1_two_holders_conflict_and_admit_names_claim() -> None:
    """Falsifier: two holders of one work_key on two lanes with no 409."""
    ledger = _ledger()
    first = claim(_KEY, "seat-a", "L1", ledger=ledger)
    with pytest.raises(WorkKeyHeld) as held:
        claim(_KEY, "seat-b", "L2", ledger=ledger)
    assert held.value.holder["holder_dispatch_id"] == first["claim_id"]
    assert held.value.holder["holder_agent"] == "seat-a"
    with pytest.raises(SourceRefConflict) as conflict:
        _admit(ledger, dispatch_id="disp-l2", thread_id="L2")
    assert conflict.value.holder_kind == "claim"
    assert conflict.value.holder_dispatch_id == first["claim_id"]
    client = _client()
    resp = client.post(
        "/api/v1/cursor/claim",
        json={
            "work_key": _KEY,
            "holder_agent": "seat-b",
            "thread_id": "L2",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "WORK_KEY_HELD"
    assert resp.json()["holder_dispatch_id"] == first["claim_id"]


def test_t1_admit_then_claim_names_the_dispatch() -> None:
    ledger = _ledger()
    _admit(ledger, dispatch_id="disp-live", thread_id="L1")
    with pytest.raises(WorkKeyHeld) as held:
        claim(_KEY, "seat-b", "L2", ledger=ledger)
    assert held.value.holder["holder_dispatch_id"] == "disp-live"
    assert held.value.holder["holder_kind"] == "implement"


def test_t2_admit_under_own_claim_id() -> None:
    ledger = _ledger()
    first = claim(_KEY, "seat-a", "L1", ledger=ledger)
    _admit(
        ledger,
        dispatch_id="disp-successor",
        thread_id="L2",
        claim_id=first["claim_id"],
    )
    assert _status(ledger, "disp-successor") == "admitted"


def test_t3_racing_claims_one_row() -> None:
    ledger = _ledger()
    errors: list[BaseException] = []
    wins: list[dict] = []

    def _go(agent: str, thread: str) -> None:
        try:
            wins.append(claim(_KEY, agent, thread, ledger=ledger))
        except WorkKeyHeld as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=_go, args=("seat-a", "L1")),
        threading.Thread(target=_go, args=("seat-b", "L2")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(wins) == 1
    assert len(errors) == 1
    with ledger._connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM cursor_sdk_dispatches WHERE status='claimed'"
        ).fetchone()
    assert int(count["n"]) == 1


def _expire(ledger: CursorDispatchLedger, claim_id: str) -> None:
    past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET claim_expires_at=? WHERE dispatch_id=?",
            (past, claim_id),
        )


def test_t4_renew_and_expired_does_not_block() -> None:
    ledger = _ledger()
    first = claim(_KEY, "seat-a", "L1", ttl_s=120, ledger=ledger)
    again = claim(_KEY, "seat-a", "L1", ttl_s=180, ledger=ledger)
    assert again["claim_id"] == first["claim_id"]
    assert again["renewed"] is True
    assert again["holder"]["expires_at"] > first["holder"]["expires_at"]
    admit_key = "todo:expired-admit"
    admit_claim = claim(admit_key, "seat-a", "L1", ledger=ledger)
    _expire(ledger, admit_claim["claim_id"])
    _admit(ledger, work_key=admit_key, dispatch_id="disp-after-expiry", thread_id="L9")
    other_key = "todo:expired-other"
    other_claim = claim(other_key, "seat-a", "L1", ledger=ledger)
    _expire(ledger, other_claim["claim_id"])
    taken = claim(other_key, "seat-b", "L2", ledger=ledger)
    assert taken["claim_id"] == claim_id_for(
        work_key=other_key, holder_agent="seat-b", thread_id="L2"
    )
    revive_key = "todo:expired-revive"
    original = claim(revive_key, "seat-a", "L1", ledger=ledger)
    _expire(ledger, original["claim_id"])
    revived = claim(revive_key, "seat-a", "L1", ledger=ledger)
    assert revived["claim_id"] == original["claim_id"]
    assert revived["renewed"] is False
    assert _status(ledger, original["claim_id"]) == "claimed"


def test_t5_release_outcomes_and_idempotent_repeat() -> None:
    ledger = _ledger()
    client = _client()
    first = claim(_KEY, "seat-a", "L1", ledger=ledger)
    missing = client.post(
        "/api/v1/cursor/claim/claim-missing/release",
        json={"holder_agent": "seat-a", "outcome": "completed"},
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "CLAIM_NOT_FOUND"
    wrong = client.post(
        f"/api/v1/cursor/claim/{first['claim_id']}/release",
        json={"holder_agent": "seat-b", "outcome": "failed"},
    )
    assert wrong.status_code == 409
    assert wrong.json()["code"] == "CLAIM_NOT_HELD"
    done = release(first["claim_id"], "seat-a", "withdrawn", ledger=ledger)
    assert done["status"] == "cancelled"
    assert done["already_released"] is False
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT status, terminal_status, record_json FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            (first["claim_id"],),
        ).fetchone()
    assert row["status"] == "cancelled"
    assert row["terminal_status"] == "cancelled"
    assert json.loads(row["record_json"])["claim_release"] == "withdrawn"
    again = client.post(
        f"/api/v1/cursor/claim/{first['claim_id']}/release",
        json={"holder_agent": "seat-a", "outcome": "withdrawn"},
    )
    assert again.status_code == 200
    assert again.json()["already_released"] is True
    other_outcome = client.post(
        f"/api/v1/cursor/claim/{first['claim_id']}/release",
        json={"holder_agent": "seat-a", "outcome": "completed"},
    )
    assert other_outcome.status_code == 409


def test_t6_expire_posts_fact_and_poster_failure_sticks() -> None:
    ledger = _ledger()
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    claim(_KEY, "seat-a", "L1", ledger=ledger, now=past, ttl_s=60)
    calls: list[dict] = []
    rows = expire_claims(
        datetime.now(UTC).isoformat(), ledger=ledger, poster=calls.append
    )
    assert len(rows) == 1
    assert len(calls) == 1
    assert calls[0]["fact_thread"] == "15851"
    assert calls[0]["fact_to"] == "gc1"
    assert calls[0]["fact_from"] == "git-integration-worker"
    assert calls[0]["body"].startswith("FACT: claim ")
    assert "claim_release=expired" in calls[0]["body"]
    assert calls[0]["subject"].startswith("FACT|claim-expired|")
    cid = calls[0]["claim_id"]
    assert _status(ledger, cid) == "cancelled"
    with ledger._connect() as conn:
        record = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (cid,),
        ).fetchone()
    assert json.loads(record["record_json"])["claim_release"] == "expired"

    def _boom(_row: dict) -> None:
        raise RuntimeError("bus down")

    claim("todo:second-claim", "seat-a", "L3", ledger=ledger, now=past, ttl_s=60)
    expire_claims(datetime.now(UTC).isoformat(), ledger=ledger, poster=_boom)
    with ledger._connect() as conn:
        left = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE work_key=?",
            ("todo:second-claim",),
        ).fetchone()
    assert left["status"] == "cancelled"


def test_t7_claimed_invisible_to_open_holder_consumers() -> None:
    before = _active_by_lane()
    ledger = _ledger()
    first = claim(_KEY, "seat-a", "L1", ledger=ledger)
    envelope = sdk_busy_status_envelope()
    assert envelope["active_holder"] is None
    assert envelope["queue_depth"] == 0
    assert _active_by_lane() == before
    assert (
        ledger.stale_writers(
            threshold_s=1,
            dead_run_grace_s=1,
            worker_instance="t",
        )
        == []
        or first["claim_id"] not in ledger.orphan_holders()
    )
    assert first["claim_id"] not in ledger.orphan_holders()
    assert ledger.release_stale_writer(dispatch_id=first["claim_id"]) is None
    assert _status(ledger, first["claim_id"]) == "claimed"
    assert _bridge_is_owned("claim-x", lambda _did: {"status": "claimed"}) is False
    assert "claimed" in _REMIT_SKIP_STATUSES
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status='parked_waiting', "
            "park_child_dispatch_id=? WHERE dispatch_id=?",
            (first["claim_id"], first["claim_id"]),
        )
    # The claim row is not a park parent. Seed a separate parked parent whose
    # child id is the claim, then boot-reconcile. The child must stay claimed.
    _admit(
        ledger, work_key="todo:park-parent", dispatch_id="parent-park", thread_id="P1"
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status='parked_waiting', "
            "park_child_dispatch_id=? WHERE dispatch_id='parent-park'",
            (first["claim_id"],),
        )
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status='claimed', "
            "park_child_dispatch_id=NULL WHERE dispatch_id=?",
            (first["claim_id"],),
        )
    ledger.startup_reconcile(worker_instance="t")
    assert _status(ledger, first["claim_id"]) == "claimed"


def test_claim_post_still_delegates_to_claim_module_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ledger route registration must not alter claim() delegation (ACK #5)."""
    seen: list[str] = []

    def _spy_claim(**kwargs: object) -> dict:
        seen.append(str(kwargs.get("work_key")))
        return {"claim_id": "claim-spy", "work_key": kwargs.get("work_key")}

    monkeypatch.setattr(
        "services.git_integration_worker.routes.cursor_claim.claim",
        _spy_claim,
    )
    client = _client()
    resp = client.post(
        "/api/v1/cursor/claim",
        json={
            "work_key": _KEY,
            "holder_agent": "seat-a",
            "thread_id": "16006",
        },
    )
    assert resp.status_code == 200
    assert seen == [_KEY]


def test_t8_occupancy_without_bus_and_projection_table() -> None:
    ledger = _ledger()
    client = _client()
    first = claim(_KEY, "seat-a", "L1", ledger=ledger)
    live = client.get("/api/v1/cursor/occupancy", params={"work_key": _KEY})
    assert live.status_code == 200
    body = live.json()
    assert body["state"] == "acting"
    assert body["holder"]["kind"] == "claim"
    assert body["holder"]["id"] == first["claim_id"]
    assert body["label"] == "occupancy, not progress"
    assert "landed" not in body
    assert "done" not in body
    assert "progress" not in body
    release(first["claim_id"], "seat-a", "completed", ledger=ledger)
    idle = client.get("/api/v1/cursor/occupancy", params={"work_key": _KEY})
    assert idle.json()["state"] == "idle"
    assert "landed" not in idle.json()

    cases = [
        ("todo:occ-parked-waiting", "parked_waiting", None, "parked"),
        ("todo:occ-restart", "cancelled", "restart", "parked"),
        ("todo:occ-budget", "completed", "budget", "parked"),
        ("todo:occ-claim", "claimed", None, "acting"),
        ("todo:occ-running", "running", None, "acting"),
        ("todo:occ-pinned", "completed", "pinned", "pinned"),
        ("todo:occ-done", "completed", None, "idle"),
    ]
    for key, status, kind, expect in cases:
        _seed_occupancy(ledger, key, status, kind)
        got = client.get("/api/v1/cursor/occupancy", params={"work_key": key})
        assert got.status_code == 200
        payload = got.json()
        assert payload["state"] == expect, key
        assert "landed" not in payload
        assert "done" not in payload
        assert "progress" not in payload
        if expect == "pinned":
            assert payload["stop"] == "ROW_PINNED"
            assert payload["holder"] is None


def _seed_occupancy(
    ledger: CursorDispatchLedger, key: str, status: str, kind: str | None
) -> None:
    dispatch_id = f"row-{key.split(':', 1)[1]}"
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    record: dict[str, object] = {}
    contract = "implement"
    if kind == "budget":
        contract = "conductor"
        record = {"hop_parked": True, "hop_seq": 1, "hop_reason": "silent"}
    if kind == "pinned":
        contract = "conductor"
        record = {"closeout_stop_tokens": ["ROW_PINNED"]}
    if status == "claimed":
        claim(key, "seat-a", "L1", ledger=ledger)
        return
    with ledger._connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, caller_agent, resolved_model, "
            " message_present, status, record_json, contract, read_only, queued_at, "
            " work_key, identity_class, terminal_status, terminal_at, park_kind, "
            " park_expires_at) "
            "VALUES (?, ?, 'T', 'seat-a', 'none', 0, ?, ?, ?, 1, ?, ?, 'declared', "
            "?, ?, ?, ?)",
            (
                dispatch_id,
                dispatch_id,
                status,
                json.dumps(record),
                contract,
                datetime.now(UTC).isoformat(),
                key,
                status if status in ("completed", "failed", "cancelled") else None,
                datetime.now(UTC).isoformat()
                if status in ("completed", "failed", "cancelled")
                else None,
                "park_for_restart" if kind == "restart" else None,
                future if kind == "restart" else None,
            ),
        )


def test_t9_check_migration_preserves_rows_and_is_idempotent(tmp_path) -> None:
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript(_OLD_DDL)
    conn.execute("CREATE INDEX idx_custom_fp ON cursor_sdk_dispatches(fingerprint)")
    conn.execute(
        "INSERT INTO cursor_sdk_dispatches "
        "(dispatch_id, fingerprint, thread_id, resolved_model, status, record_json) "
        "VALUES ('keep-1', 'fp', 'th', 'none', 'running', '{}')"
    )
    conn.commit()
    _migrate_claimed_status(conn)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='cursor_sdk_dispatches'"
    ).fetchone()["sql"]
    assert "'claimed'" in sql
    row = conn.execute(
        "SELECT dispatch_id, status FROM cursor_sdk_dispatches WHERE dispatch_id='keep-1'"
    ).fetchone()
    assert row["status"] == "running"
    names = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='cursor_sdk_dispatches'"
        )
    }
    assert "idx_sdk_dispatch_running" in names
    assert "idx_sdk_dispatch_execution" in names
    assert "idx_custom_fp" in names
    snapshot = sql
    _migrate_claimed_status(conn)
    again = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='cursor_sdk_dispatches'"
    ).fetchone()["sql"]
    assert again == snapshot
    conn.close()


def test_claim_route_rejects_bad_work_key() -> None:
    resp = _client().post(
        "/api/v1/cursor/claim",
        json={"work_key": "nope", "holder_agent": "seat-a", "thread_id": "L1"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "INVALID_WORK_KEY"


_GROK_AGENT = "e059a74c-2498-40c9-8189-5dc939a2cce2"
_GROK_ADDRESS = {
    "kind": "grok_bot",
    "agent_id": _GROK_AGENT,
    "display": ":10",
    "app": "grok",
}


def test_registry_s1_claim_address_occupancy_and_renew_merge() -> None:
    ledger = _ledger()
    client = _client()
    key_a = "todo:registry-s1-a"
    key_b = "todo:registry-s1-b"
    claim(
        key_a,
        "seat-a",
        "L1",
        ledger=ledger,
        seat_name="gc1-grok",
        address=_GROK_ADDRESS,
        work_status="active",
    )
    occ = client.get("/api/v1/cursor/occupancy", params={"work_key": key_a})
    assert occ.status_code == 200
    holder = occ.json()["holder"]
    assert holder["seat_name"] == "gc1-grok"
    assert holder["address"]["agent_id"] == _GROK_AGENT
    assert holder["address"]["display"] == "10"
    assert holder["address"]["app"] == "grok"

    claim(
        key_a,
        "seat-a",
        "L1",
        ledger=ledger,
        seat_name="gc1-grok-renamed",
        address={"kind": "grok_bot", "agent_id": _GROK_AGENT, "hidden": True},
    )
    again = client.get("/api/v1/cursor/occupancy", params={"work_key": key_a})
    merged = again.json()["holder"]
    assert merged["seat_name"] == "gc1-grok-renamed"
    assert merged["address"]["agent_id"] == _GROK_AGENT
    assert merged["address"].get("hidden") is True
    assert merged["address"]["display"] == "10"

    claim(
        key_b,
        "seat-a",
        "L2",
        ledger=ledger,
        seat_name="seat-b",
        address={"tab_url": "https://example.com/tab"},
    )
    reg = registry(ledger=ledger, work_key_prefix="todo:registry-s1")
    assert reg["entries"][0]["work_key"] == key_b
    assert reg["entries"][1]["work_key"] == key_a
    assert reg["entries"][0]["address"]["tab_url"].startswith("https://")
    assert reg["entries"][1]["address"]["agent_id"] == _GROK_AGENT


def test_registry_s1_update_landed_and_holder_guard() -> None:
    ledger = _ledger()
    client = _client()
    row = claim("todo:registry-update", "seat-a", "L1", ledger=ledger)
    cid = row["claim_id"]
    wrong = client.post(
        f"/api/v1/cursor/claim/{cid}/update",
        json={"holder_agent": "seat-b", "work_status": "active"},
    )
    assert wrong.status_code == 409
    assert wrong.json()["code"] == "CLAIM_NOT_HELD"
    landed = client.post(
        f"/api/v1/cursor/claim/{cid}/update",
        json={"holder_agent": "seat-a", "work_status": "landed"},
    )
    assert landed.status_code == 200
    assert landed.json()["released"]["status"] == "completed"
    assert _status(ledger, cid) == "completed"


def test_acting_dispatch_row_claim_update_landed() -> None:
    """Auto-recorded dispatch rows (queued/admitted/running) accept claim_update landed."""
    ledger = _ledger()
    dispatch_id = "disp-acting-land-22537"
    _admit(ledger, dispatch_id=dispatch_id, work_key="todo:acting-land-22537")
    result = update(
        dispatch_id,
        "seat-a",
        work_status="landed",
        caller_dispatch_id=dispatch_id,
        ledger=ledger,
    )
    assert result["released"]["status"] == "completed"
    assert _status(ledger, dispatch_id) == "completed"


def test_acting_row_claim_update_without_caller_dispatch_id_refused() -> None:
    ledger = _ledger()
    dispatch_id = "disp-acting-missing-caller-42965"
    _admit(ledger, dispatch_id=dispatch_id, work_key="todo:acting-missing-caller")
    with pytest.raises(ClaimError) as excinfo:
        update(
            dispatch_id,
            "seat-a",
            work_status="landed",
            ledger=ledger,
        )
    assert excinfo.value.code == "CLAIM_NOT_HELD"


def test_acting_row_own_stamp_claim_update_ok() -> None:
    ledger = _ledger()
    dispatch_id = "disp-acting-own-stamp-42965"
    _admit(ledger, dispatch_id=dispatch_id, work_key="todo:acting-own-stamp")
    out = update(
        dispatch_id,
        "seat-a",
        work_status="active",
        note="progress",
        caller_dispatch_id=dispatch_id,
        ledger=ledger,
    )
    assert out["claim_id"] == dispatch_id


def test_running_row_claim_landed_closeout_merge_survives_terminal_paths() -> None:
    """Early claim_update landed must not block later executor closeout record merge.

    Breaks when a registry landed flip leaves guarded terminal marks or
    already_terminal cancel paths that drop or clobber closeout fields.
    """
    ledger = _ledger()
    dispatch_id = "disp-landed-closeout-42965"
    work_key = "todo:landed-closeout-42965"
    _admit(ledger, dispatch_id=dispatch_id, work_key=work_key)
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status='running' WHERE dispatch_id=?",
            (dispatch_id,),
        )
        conn.commit()
    update(
        dispatch_id,
        "seat-a",
        work_status="landed",
        caller_dispatch_id=dispatch_id,
        ledger=ledger,
    )
    assert _status(ledger, dispatch_id) == "completed"
    closeout_patch = {
        "closeout_stop_tokens": ["status: complete"],
        "work_verdict": "shipped",
    }
    ledger.merge_record_json(dispatch_id=dispatch_id, patch=closeout_patch)
    guarded = ledger.mark_terminal_checked(
        dispatch_id=dispatch_id,
        terminal_status="completed",
        only_from_statuses=("queued", "admitted", "running"),
    )
    assert guarded.marked is False
    cancel = ledger.cancel_dispatch(dispatch_id=dispatch_id)
    assert cancel.outcome == "already_terminal"
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT status, record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    assert row is not None
    rec = json.loads(row["record_json"])
    assert rec["work_verdict"] == "shipped"
    assert row["status"] == "completed"


def test_cross_dispatch_release_refused_with_mismatched_caller_dispatch_id() -> None:
    ledger = _ledger()
    other_id = "disp-other-live-42965"
    _admit(ledger, dispatch_id=other_id, work_key="todo:cross-dispatch-42965")
    with pytest.raises(ClaimError) as excinfo:
        release(
            other_id,
            "seat-a",
            "completed",
            caller_dispatch_id="stamp-not-other",
            ledger=ledger,
        )
    assert excinfo.value.code == "CLAIM_NOT_HELD"


def test_registry_s1_invalid_address_and_secret_key() -> None:
    client = _client()
    bad_shape = client.post(
        "/api/v1/cursor/claim",
        json={
            "work_key": _KEY,
            "holder_agent": "seat-a",
            "thread_id": "L1",
            "address": {"kind": "grok_bot"},
        },
    )
    assert bad_shape.status_code == 422
    assert bad_shape.json()["code"] == "ADDRESS_UNREACHABLE_SHAPE"
    secret = client.post(
        "/api/v1/cursor/claim",
        json={
            "work_key": _KEY,
            "holder_agent": "seat-a",
            "thread_id": "L1",
            "address": {
                "kind": "grok_bot",
                "agent_id": _GROK_AGENT,
                "api_key": "nope",
            },
        },
    )
    assert secret.status_code == 422
    assert secret.json()["code"] == "ADDRESS_UNREACHABLE_SHAPE"


def test_registry_s1_grok_bot_agent_id_only_and_assertion_work_key() -> None:
    ledger = _ledger()
    minimal = claim(
        "packet:smoke-grok",
        "seat-a",
        "L1",
        ledger=ledger,
        address={"kind": "grok_bot", "agent_id": _GROK_AGENT},
    )
    assert minimal["holder"]["address"]["agent_id"] == _GROK_AGENT
    assert minimal["holder"]["address"]["app"] == "grok"
    reg = registry(ledger=ledger, work_key_prefix="packet:smoke")
    entry = reg["entries"][0]
    assert entry["address"] == {
        "kind": "grok_bot",
        "agent_id": _GROK_AGENT,
        "app": "grok",
    }
    claim("a:21631", "seat-a", "L9", ledger=ledger)
    mapped = registry(ledger=ledger, work_key_prefix="a:")
    assert mapped["entries"][0]["work_key_display"] == "decision:a-21631"


def test_registry_s1_byte_cap_truncated() -> None:
    ledger = _ledger()
    for idx in range(40):
        claim(
            f"todo:registry-cap-{idx:02d}",
            "seat-a",
            f"L{idx}",
            ledger=ledger,
            address={"tab_url": f"https://example.com/{idx}/{'x' * 120}"},
        )
    reg = registry(ledger=ledger, work_key_prefix="todo:registry-cap")
    assert reg["truncated"] is True
    assert (
        len(json.dumps(reg, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        <= 5120
    )


def test_registry_s1_abandoned_releases_withdrawn() -> None:
    ledger = _ledger()
    row = claim("todo:registry-abandon", "seat-a", "L1", ledger=ledger)
    out = update(row["claim_id"], "seat-a", work_status="abandoned", ledger=ledger)
    assert out["released"]["status"] == "cancelled"


def test_registry_state_all_includes_terminal_rows() -> None:
    ledger = _ledger()
    client = _client()
    _admit(ledger, dispatch_id="disp-live-all", thread_id="T1", work_key="todo:reg-all-live")
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status='completed', "
            "terminal_status='completed', terminal_at=? WHERE dispatch_id=?",
            (datetime.now(UTC).isoformat(), "disp-live-all"),
        )
    live = registry(ledger=ledger, state="live", work_key_prefix="todo:reg-all")
    assert live["state"] == "live"
    assert live["entries"] == []
    all_rows = registry(ledger=ledger, state="all", work_key_prefix="todo:reg-all")
    assert all_rows["state"] == "all"
    assert len(all_rows["entries"]) == 1
    assert all_rows["entries"][0]["status"] == "completed"
    bad = client.get("/api/v1/cursor/registry", params={"state": "released"})
    assert bad.status_code == 422


def test_registry_auto_record_stamps_dispatch_row() -> None:
    from services.git_integration_worker.cursor_sdk_work_registry_admit import (
        build_cursor_sdk_registry_address,
        record_dispatch_registry_best_effort,
    )

    ledger = _ledger()
    _admit(
        ledger,
        dispatch_id="disp-auto-reg",
        thread_id="21650",
        work_key="todo:work-registry-s3",
    )
    with ledger._connect() as conn:
        before = conn.execute(
            "SELECT caller_agent, record_json FROM cursor_sdk_dispatches "
            "WHERE dispatch_id='disp-auto-reg'"
        ).fetchone()
    before_rec = json.loads(before["record_json"])
    assert before_rec.get("seat_name") is None
    assert before_rec.get("address") is None
    row_id = record_dispatch_registry_best_effort(
        dispatch_id="disp-auto-reg",
        thread_id="21650",
        caller_agent="cursor-sdk",
        seat_name="cursor-sdk",
        address=build_cursor_sdk_registry_address(
            dispatch_id="disp-auto-reg",
            worker_thread="21650",
            worktree="/tmp/lane-21650",
        ),
        work_key="todo:work-registry-s3",
        ledger=ledger,
    )
    assert row_id == "disp-auto-reg"
    with ledger._connect() as conn:
        after = conn.execute(
            "SELECT caller_agent, record_json FROM cursor_sdk_dispatches "
            "WHERE dispatch_id='disp-auto-reg'"
        ).fetchone()
    rec = json.loads(after["record_json"])
    assert rec["seat_name"] == "cursor-sdk"
    assert rec["address"]["kind"] == "cursor_sdk"
    assert rec["address"]["worktree"] == "/tmp/lane-21650"


def test_registry_next_hint_and_cursor_sdk_address_kind() -> None:
    from services.git_integration_worker.cursor_sdk_claim_address import normalize_address
    from services.git_integration_worker.cursor_sdk_work_registry_admit import (
        registry_next_hint,
    )

    norm = normalize_address(
        {
            "kind": "cursor_sdk",
            "dispatch_id": "d1",
            "worker_thread": "15851",
            "worktree": "/wt/lane",
        }
    )
    assert norm["dispatch_id"] == "d1"
    hint = registry_next_hint(row_id="disp-abc")
    assert "disp-abc" in hint
    assert "team_dispatch(op=registry" in hint
