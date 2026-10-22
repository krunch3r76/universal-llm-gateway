"""Lane-safety for cse_session_holders.upsert_holder."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.git_integration_worker.cse_session_holders import (
    HolderLaneConflictError,
    ensure_schema,
    get_holder,
    upsert_holder,
)
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.routes.admin import router

_HOUSE = "https://claude.ai/cowork/cse_019nCuLaneHouse"
_HOP = "https://claude.ai/cowork/cse_019nCuLaneHop"
_RELEASED = "https://claude.ai/cowork/cse_019nCuLaneRel"


@pytest.fixture()
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CursorDispatchLedger:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    return CursorDispatchLedger.instance()


def test_cross_lane_steal_refused(
    ledger: CursorDispatchLedger, monkeypatch: pytest.MonkeyPatch
) -> None:
    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders._emit",
        lambda signal, payload: emitted.append((signal, payload)),
    )
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(conn, chat_url=_HOUSE, lane_thread_id="12286")
        with pytest.raises(HolderLaneConflictError) as raised:
            upsert_holder(conn, chat_url=_HOUSE, lane_thread_id="16416")
        row = get_holder(conn, "cse_019nCuLaneHouse")
    assert raised.value.current_lane == "12286"
    assert raised.value.requested_lane == "16416"
    assert row is not None
    assert row["lane_thread_id"] == "12286"
    assert row["seat_state"] == "driving"
    assert any(sig == "cse.holder.lane_conflict" for sig, _ in emitted)


def test_upsert_route_maps_lane_conflict_to_409(ledger: CursorDispatchLedger) -> None:
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(conn, chat_url=_HOUSE, lane_thread_id="12286")
        conn.commit()
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/git/admin/cse-holder/upsert",
            json={"chat_url": _HOUSE, "lane_thread_id": "16416"},
        )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["current_lane"] == "12286"
    assert detail["requested_lane"] == "16416"
    with ledger._connect() as conn:
        row = get_holder(conn, "cse_019nCuLaneHouse")
    assert row is not None and row["lane_thread_id"] == "12286"


def test_same_lane_hop_supersedes_predecessor(ledger: CursorDispatchLedger) -> None:
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(conn, chat_url=_HOUSE, lane_thread_id="12286")
        upsert_holder(conn, chat_url=_HOP, lane_thread_id="12286")
        house = get_holder(conn, "cse_019nCuLaneHouse")
        hop = get_holder(conn, "cse_019nCuLaneHop")
        driving = conn.execute(
            "SELECT holder_id FROM cse_session_holders "
            "WHERE lane_thread_id=? AND seat_state='driving'",
            ("12286",),
        ).fetchall()
    assert house is not None and house["seat_state"] == "superseded"
    assert house["superseded_by"] == "cse_019nCuLaneHop"
    assert hop is not None and hop["seat_state"] == "driving"
    assert [row["holder_id"] for row in driving] == ["cse_019nCuLaneHop"]


def test_released_row_rebinds_to_new_lane(ledger: CursorDispatchLedger) -> None:
    with ledger._connect() as conn:
        ensure_schema(conn)
        upsert_holder(conn, chat_url=_RELEASED, lane_thread_id="12286")
        conn.execute(
            "UPDATE cse_session_holders SET seat_state='released' WHERE holder_id=?",
            ("cse_019nCuLaneRel",),
        )
        rebound = upsert_holder(conn, chat_url=_RELEASED, lane_thread_id="16416")
    assert rebound["lane_thread_id"] == "16416"
    assert rebound["seat_state"] == "driving"


def test_same_lane_refresh_is_idempotent(ledger: CursorDispatchLedger) -> None:
    with ledger._connect() as conn:
        ensure_schema(conn)
        first = upsert_holder(
            conn,
            chat_url=_HOUSE,
            lane_thread_id="12286",
            registration_id="reg-1",
        )
        second = upsert_holder(
            conn,
            chat_url=_HOUSE,
            lane_thread_id="12286",
            registration_id="reg-1",
        )
        driving = conn.execute(
            "SELECT COUNT(*) AS n FROM cse_session_holders "
            "WHERE lane_thread_id=? AND seat_state='driving'",
            ("12286",),
        ).fetchone()
    assert first["holder_id"] == second["holder_id"]
    assert second["lane_thread_id"] == "12286"
    assert second["seat_state"] == "driving"
    assert second["superseded_by"] is None
    assert driving["n"] == 1
