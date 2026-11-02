"""Work registry auto-record and terminal work_status stamps."""

from __future__ import annotations

import json

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_work_registry_admit import (
    close_frontier_registry_row,
    record_dispatch_registry_best_effort,
    reap_stale_frontier_registry_rows,
    stamp_registry_terminal_work_status,
    upsert_frontier_registry_dispatch,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline


def test_upsert_frontier_row_for_orion_like_seat() -> None:
    ledger = CursorDispatchLedger.instance()
    rid = upsert_frontier_registry_dispatch(
        dispatch_id="exec-orion-1",
        thread_id="15851",
        caller_agent="orion",
        seat_name="orion",
        address={"kind": "api_role", "thread_id": "15851"},
        work_key="todo:orion-task",
        resolved_model="openai/gpt-5",
        contract="consult",
        ledger=ledger,
    )
    assert rid == "exec-orion-1"
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT caller_agent, record_json, status FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            ("exec-orion-1",),
        ).fetchone()
    assert row["caller_agent"] == "orion"
    rec = json.loads(row["record_json"])
    assert rec["seat_name"] == "orion"
    assert rec["address"]["kind"] == "api_role"


def test_terminal_stamp_sets_landed_on_completed() -> None:
    ledger = CursorDispatchLedger.instance()
    record_dispatch_registry_best_effort(
        dispatch_id="disp-term-1",
        thread_id="T1",
        caller_agent="cursor-sdk",
        seat_name="cursor-sdk",
        address={"kind": "cursor_sdk", "dispatch_id": "disp-term-1", "worker_thread": "T1"},
        ledger=ledger,
    )
    ledger.mark_terminal(dispatch_id="disp-term-1", terminal_status="completed")
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("disp-term-1",),
        ).fetchone()
    rec = json.loads(row["record_json"])
    assert rec.get("work_status") == "landed"


def test_registry_record_route() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from services.git_integration_worker.routes.cursor_claim import router

    app = FastAPI()
    app.include_router(router)
    tc = TestClient(app)
    resp = tc.post(
        "/api/v1/cursor/registry/record",
        json={
            "dispatch_id": "disp-route-1",
            "thread_id": "21650",
            "caller_agent": "cursor-sdk",
            "seat_name": "cursor-sdk",
            "address": {
                "kind": "cursor_sdk",
                "dispatch_id": "disp-route-1",
                "worker_thread": "21650",
            },
            "work_key": "todo:route-record",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["registry_row_id"] == "disp-route-1"
    assert "_next" in body


@pytest.fixture
def isolated_registry_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "registry.sqlite"
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_LEDGER", str(db))
    CursorDispatchLedger._instance = None
    return CursorDispatchLedger.instance()


def test_close_frontier_row_admitted_to_failed_frees_work_key(
    isolated_registry_ledger: CursorDispatchLedger,
) -> None:
    key = "todo:frontier-close-frees-key"
    upsert_frontier_registry_dispatch(
        dispatch_id="exec-close-1",
        thread_id="21792",
        caller_agent="maestro",
        seat_name="maestro",
        address={"kind": "api_role", "thread_id": "21792"},
        work_key=key,
        ledger=isolated_registry_ledger,
    )
    outcome = close_frontier_registry_row(
        "exec-close-1", "failed", "test_close", ledger=isolated_registry_ledger
    )
    assert outcome == {"ok": True, "marked": True}
    req = CursorDispatchRequest(
        thread_id="21792",
        model="composer-2.5",
        dispatch_id="disp-after-close",
        execution_id="exec-after-close",
        packet_path="tmp/packet.md",
        work_key=key,
    )
    isolated_registry_ledger.admit(
        req=req,
        fingerprint=isolated_registry_ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor-sdk",
        resolved_model=req.model,
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id=req.model,
        ),
        work_key=key,
    )


def test_close_frontier_row_does_not_overwrite_cancelled(
    isolated_registry_ledger: CursorDispatchLedger,
) -> None:
    upsert_frontier_registry_dispatch(
        dispatch_id="exec-cancel-hold",
        thread_id="21792",
        caller_agent="maestro",
        seat_name="maestro",
        address={"kind": "cdp", "thread_id": "21792"},
        work_key="todo:cancel-hold",
        ledger=isolated_registry_ledger,
    )
    with isolated_registry_ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status='cancelled', "
            "terminal_status='cancelled', "
            "record_json=json_set(COALESCE(record_json, '{}'), "
            "'$.terminal_reason', 'operator_cancel') "
            "WHERE dispatch_id=?",
            ("exec-cancel-hold",),
        )
        conn.commit()
    outcome = close_frontier_registry_row(
        "exec-cancel-hold", "failed", "should_not_apply", ledger=isolated_registry_ledger
    )
    assert outcome == {"ok": True, "marked": False}
    with isolated_registry_ledger._connect() as conn:
        row = conn.execute(
            "SELECT status, record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("exec-cancel-hold",),
        ).fetchone()
    assert row["status"] == "cancelled"
    import json

    rec = json.loads(row["record_json"] or "{}")
    assert rec.get("terminal_reason") == "operator_cancel"


def test_close_frontier_row_idempotent_second_call_marked_false(
    isolated_registry_ledger: CursorDispatchLedger,
) -> None:
    upsert_frontier_registry_dispatch(
        dispatch_id="exec-idem",
        thread_id="21792",
        caller_agent="maestro",
        seat_name="maestro",
        address={"kind": "api_role", "thread_id": "21792"},
        work_key="todo:idem",
        ledger=isolated_registry_ledger,
    )
    first = close_frontier_registry_row(
        "exec-idem", "failed", "once", ledger=isolated_registry_ledger
    )
    second = close_frontier_registry_row(
        "exec-idem", "failed", "twice", ledger=isolated_registry_ledger
    )
    assert first == {"ok": True, "marked": True}
    assert second == {"ok": True, "marked": False}


def test_registry_terminal_route_tombstones_missing_row() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from services.git_integration_worker.routes.cursor_claim import router

    app = FastAPI()
    app.include_router(router)
    tc = TestClient(app)
    resp = tc.post(
        "/api/v1/cursor/registry/terminal",
        json={
            "dispatch_id": "missing-row-xyz",
            "terminal_status": "failed",
            "reason": "test",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "marked": True}


def test_registry_record_after_api_completion_does_not_insert_admitted(
    isolated_registry_ledger: CursorDispatchLedger,
) -> None:
    exec_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    key = "todo:race-after-completion"
    isolated_registry_ledger.upsert_api_role_completed_dispatch(
        execution_id=exec_id,
        work_key=key,
        resolved_model="openai/gpt-5",
        contract="consult",
        record_json='{"substrate":"api_role"}',
        stamp="2026-11-02T08:00:00+00:00",
        terminal_status="completed",
    )
    row_id = record_dispatch_registry_best_effort(
        dispatch_id=exec_id,
        thread_id="21792",
        caller_agent="maestro",
        seat_name="maestro",
        address={"kind": "api_role", "thread_id": "21792"},
        work_key=key,
        ledger=isolated_registry_ledger,
    )
    assert row_id == f"api-{exec_id}"
    with isolated_registry_ledger._connect() as conn:
        rows = conn.execute(
            "SELECT dispatch_id, status FROM cursor_sdk_dispatches "
            "WHERE work_key=?",
            (key,),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"


def test_reaper_fails_stale_api_role_and_cdp_rows(
    isolated_registry_ledger: CursorDispatchLedger,
) -> None:
    from datetime import UTC, datetime, timedelta

    from services.git_integration_worker.cursor_dispatch_ledger import _now

    stale = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    for dispatch_id, kind in (("stale-api", "api_role"), ("stale-cdp", "cdp")):
        upsert_frontier_registry_dispatch(
            dispatch_id=dispatch_id,
            thread_id="21792",
            caller_agent="maestro",
            seat_name="maestro",
            address={"kind": kind, "thread_id": "21792"},
            work_key=f"todo:{dispatch_id}",
            ledger=isolated_registry_ledger,
        )
        with isolated_registry_ledger._connect() as conn:
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET started_at=?, queued_at=? "
                "WHERE dispatch_id=?",
                (stale, stale, dispatch_id),
            )
            conn.commit()
    reaped = reap_stale_frontier_registry_rows(
        ledger=isolated_registry_ledger, ttl_s=14400
    )
    assert set(reaped) == {"stale-api", "stale-cdp"}
    with isolated_registry_ledger._connect() as conn:
        for dispatch_id in ("stale-api", "stale-cdp"):
            row = conn.execute(
                "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
            assert row["status"] == "failed"


def test_reaper_skips_fresh_rows_and_read_only_0_rows(
    isolated_registry_ledger: CursorDispatchLedger,
) -> None:
    upsert_frontier_registry_dispatch(
        dispatch_id="fresh-api",
        thread_id="21792",
        caller_agent="maestro",
        seat_name="maestro",
        address={"kind": "api_role", "thread_id": "21792"},
        work_key="todo:fresh",
        ledger=isolated_registry_ledger,
    )
    req = CursorDispatchRequest(
        thread_id="99999",
        model="composer-2.5",
        dispatch_id="sdk-not-readonly",
        execution_id="exec-sdk",
        message="x",
        work_key="todo:sdk-row",
    )
    isolated_registry_ledger.admit(
        req=req,
        fingerprint=isolated_registry_ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor-sdk",
        resolved_model=req.model,
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id=req.model,
        ),
        work_key=req.work_key,
    )
    with isolated_registry_ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET started_at=? WHERE dispatch_id=?",
            ("2020-01-01T00:00:00+00:00", "sdk-not-readonly"),
        )
        conn.commit()
    reaped = reap_stale_frontier_registry_rows(
        ledger=isolated_registry_ledger, ttl_s=60
    )
    assert reaped == []
    with isolated_registry_ledger._connect() as conn:
        fresh = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("fresh-api",),
        ).fetchone()
        sdk = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("sdk-not-readonly",),
        ).fetchone()
    assert fresh["status"] == "admitted"
    assert sdk["status"] == "admitted"


def test_reaper_skips_already_cancelled(
    isolated_registry_ledger: CursorDispatchLedger,
) -> None:
    from datetime import UTC, datetime, timedelta

    stale = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    upsert_frontier_registry_dispatch(
        dispatch_id="cancelled-stale",
        thread_id="21792",
        caller_agent="maestro",
        seat_name="maestro",
        address={"kind": "cdp", "thread_id": "21792"},
        work_key="todo:cancel-stale",
        ledger=isolated_registry_ledger,
    )
    with isolated_registry_ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status='cancelled', "
            "terminal_status='cancelled', started_at=?, queued_at=? "
            "WHERE dispatch_id=?",
            (stale, stale, "cancelled-stale"),
        )
        conn.commit()
    reaped = reap_stale_frontier_registry_rows(
        ledger=isolated_registry_ledger, ttl_s=14400
    )
    assert reaped == []


def test_reaper_respects_cdp_max_wall_s_before_margin(
    isolated_registry_ledger: CursorDispatchLedger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime, timedelta

    monkeypatch.setenv("CURSOR_REGISTRY_FRONTIER_WALL_MARGIN_S", "900")
    five_h_ago = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    upsert_frontier_registry_dispatch(
        dispatch_id="long-cdp-leg",
        thread_id="21802",
        caller_agent="maestro",
        seat_name="maestro",
        address={"kind": "cdp", "thread_id": "21802"},
        work_key="todo:long-cdp",
        max_wall_s=43200,
        ledger=isolated_registry_ledger,
    )
    with isolated_registry_ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET started_at=?, queued_at=? "
            "WHERE dispatch_id=?",
            (five_h_ago, five_h_ago, "long-cdp-leg"),
        )
        conn.commit()
    reaped = reap_stale_frontier_registry_rows(
        ledger=isolated_registry_ledger, ttl_s=14400
    )
    assert reaped == []


def test_reaper_fails_cdp_row_past_max_wall_s_plus_margin(
    isolated_registry_ledger: CursorDispatchLedger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime, timedelta

    monkeypatch.setenv("CURSOR_REGISTRY_FRONTIER_WALL_MARGIN_S", "900")
    old = (datetime.now(UTC) - timedelta(hours=13)).isoformat()
    upsert_frontier_registry_dispatch(
        dispatch_id="expired-cdp-leg",
        thread_id="21802",
        caller_agent="maestro",
        seat_name="maestro",
        address={"kind": "cdp", "thread_id": "21802"},
        work_key="todo:expired-cdp",
        max_wall_s=43200,
        ledger=isolated_registry_ledger,
    )
    with isolated_registry_ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET started_at=?, queued_at=? "
            "WHERE dispatch_id=?",
            (old, old, "expired-cdp-leg"),
        )
        conn.commit()
    reaped = reap_stale_frontier_registry_rows(
        ledger=isolated_registry_ledger, ttl_s=14400
    )
    assert reaped == ["expired-cdp-leg"]


def test_terminal_before_record_leaves_row_terminal(
    isolated_registry_ledger: CursorDispatchLedger,
) -> None:
    dispatch_id = "cdp-terminal-first"
    close_outcome = close_frontier_registry_row(
        dispatch_id,
        "completed",
        "cdp_finalize",
        ledger=isolated_registry_ledger,
    )
    assert close_outcome == {"ok": True, "marked": True}
    row_id = upsert_frontier_registry_dispatch(
        dispatch_id=dispatch_id,
        thread_id="21802",
        caller_agent="maestro",
        seat_name="maestro",
        address={"kind": "cdp", "thread_id": "21802"},
        work_key="todo:terminal-first",
        ledger=isolated_registry_ledger,
    )
    assert row_id == dispatch_id
    with isolated_registry_ledger._connect() as conn:
        row = conn.execute(
            "SELECT status, record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    assert row["status"] == "completed"
    import json

    rec = json.loads(row["record_json"] or "{}")
    assert rec.get("terminal_reason") == "cdp_finalize"


def test_registry_record_accepts_payload_without_max_wall_s(
    isolated_registry_ledger: CursorDispatchLedger,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from services.git_integration_worker.routes.cursor_claim import router

    app = FastAPI()
    app.include_router(router)
    tc = TestClient(app)
    resp = tc.post(
        "/api/v1/cursor/registry/record",
        json={
            "dispatch_id": "no-max-wall",
            "thread_id": "21802",
            "caller_agent": "maestro",
            "address": {"kind": "api_role", "thread_id": "21802"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    with isolated_registry_ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("no-max-wall",),
        ).fetchone()
    import json

    rec = json.loads(row["record_json"] or "{}")
    assert "max_wall_s" not in rec
