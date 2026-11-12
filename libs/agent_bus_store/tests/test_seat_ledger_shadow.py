"""Shadow seat ledger — transition writer, routes, fail-open hooks."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from agent_bus_store.auth import require_token
from agent_bus_store.db import admit_dispatch, create_thread_with_turn, init_db
from agent_bus_store.db.connection import connect, now
from agent_bus_store.db.cse_associations import HOP_SEATED_BOUND_BY, associate_cse
from agent_bus_store.db.seat_drift_probes import (
    fetch_giw_holders_map as _real_fetch_giw,
)
from agent_bus_store.db.seat_ledger import (
    SeatLedgerError,
    _holder_of_chat,
    _reset_seat_ledger_counters_for_tests,
    contested_wake_quarantine_count,
    current_seat,
    durable_wake_quarantine_count,
    seat_drift_cli_lines,
    seat_drift_summary,
    seat_ledger_shadow_writes_enabled,
    shadow_hook_error_count,
    transition_seat,
)
from agent_bus_store.db.threads_atomic import close_thread
from agent_bus_store.db.turns import insert_turn
from agent_bus_store.routes.threads import seat as seat_routes
from agent_bus_store.server import create_app
from agent_bus_store.watchdog import _reap_single
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline

_URL_A = "https://claude.ai/cowork/cse_01CodB7tom1281iY8BmZJcZM"
_URL_B = "https://claude.ai/cowork/cse_01RtRztEY8bgocgT1geurtRx"
_URL_C = "https://claude.ai/cowork/cse_01AbsRegOnlyLaneChatUrl99"


@pytest.fixture()
def bus_client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    monkeypatch.setenv("AGENT_BUS_SEAT_LEDGER_SHADOW", "1")
    init_db()
    _reset_seat_ledger_counters_for_tests()
    app = create_app(db_path=str(db_path))
    app.dependency_overrides[require_token] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def _lane(client: TestClient, slug: str) -> str:
    resp = client.post("/threads", json={"slug": slug})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_transition_idempotent_and_unique_epoch(bus_client: TestClient) -> None:
    tid = _lane(bus_client, "seat-idem")
    first = transition_seat(
        thread_id=tid,
        transition="birth",
        chat_url=_URL_A,
        actor="test",
        request_id="req-1",
    )
    replay = transition_seat(
        thread_id=tid,
        transition="birth",
        chat_url=_URL_A,
        actor="test",
        request_id="req-1",
    )
    assert replay["idempotent_replay"] is True
    assert replay["id"] == first["id"]
    hop = transition_seat(
        thread_id=tid,
        transition="hop",
        chat_url=_URL_B,
        expected_epoch=first["epoch"],
        actor="test",
        request_id="req-2",
    )
    assert hop["epoch"] == first["epoch"] + 1
    with pytest.raises(SeatLedgerError) as exc:
        transition_seat(
            thread_id=tid,
            transition="hop",
            chat_url=_URL_B,
            expected_epoch=first["epoch"],
            actor="test",
            request_id="req-3",
        )
    assert exc.value.code == "seat_epoch_conflict"


def test_seat_chat_held(bus_client: TestClient) -> None:
    a = _lane(bus_client, "seat-a")
    b = _lane(bus_client, "seat-b")
    transition_seat(
        thread_id=a,
        transition="birth",
        chat_url=_URL_A,
        actor="test",
        request_id="hold-a",
    )
    with pytest.raises(SeatLedgerError) as exc:
        transition_seat(
            thread_id=b,
            transition="birth",
            chat_url=_URL_A,
            actor="test",
            request_id="hold-b",
        )
    assert exc.value.code == "seat_chat_held"


def test_contested_holds_no_chat(bus_client: TestClient) -> None:
    tid = _lane(bus_client, "seat-contested")
    row = transition_seat(
        thread_id=tid,
        transition="birth",
        contested=True,
        actor="operator:rebind",
        request_id="contested-1",
    )
    assert row["state"] == "contested"
    assert row["chat_id"] is None
    assert row["chat_url"] is None
    assert contested_wake_quarantine_count() >= 1
    durable_before = durable_wake_quarantine_count()
    _reset_seat_ledger_counters_for_tests()
    assert contested_wake_quarantine_count() == 0
    assert durable_wake_quarantine_count() == durable_before >= 1
    cur = bus_client.get(f"/threads/{tid}/seat").json()["current"]
    assert cur["state"] == "contested"
    assert cur["chat_id"] is None


def test_routes_current_history_drift(bus_client: TestClient) -> None:
    tid = _lane(bus_client, "seat-routes")
    bus_client.post(
        f"/threads/{tid}/seat",
        json={
            "transition": "birth",
            "chat_url": _URL_A,
            "actor": "test",
            "request_id": "route-birth",
        },
    )
    detail = bus_client.get(f"/threads/{tid}/seat")
    assert detail.status_code == 200
    body = detail.json()
    assert body["current"]["chat_url"] == _URL_A
    assert len(body["history"]) == 1

    drift = bus_client.get("/seats/drift")
    assert drift.status_code == 200
    assert "cse_association_mismatch_count" in drift.json()

    seats = bus_client.get("/seats?state=open")
    assert seats.status_code == 200
    assert seats.json()["count"] >= 1


def test_associate_shadow_hook_fail_open(bus_client: TestClient) -> None:
    tid = _lane(bus_client, "seat-hook-fail")
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        with patch(
            "agent_bus_store.db.seat_ledger.shadow_hook_associate_cse",
            side_effect=RuntimeError("shadow down"),
        ):
            result = associate_cse(
                thread_id=tid,
                cse_chat_url=_URL_A,
                cse_registration_id="reg-a",
            )
    assert result is not None
    assert result["state"] == "associated"
    assert shadow_hook_error_count() >= 1


def test_shadow_flag_off_is_noop(bus_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_SEAT_LEDGER_SHADOW", "0")
    assert seat_ledger_shadow_writes_enabled() is False
    tid = _lane(bus_client, "seat-flag-off")
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        result = associate_cse(
            thread_id=tid,
            cse_chat_url=_URL_A,
            cse_registration_id="reg-a",
        )
    assert result is not None
    cur = bus_client.get(f"/threads/{tid}/seat").json()["current"]
    assert cur["state"] == "none"


def test_associate_shadow_does_not_change_outcome(bus_client: TestClient) -> None:
    tid = _lane(bus_client, "seat-shadow-outcome")
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        first = associate_cse(
            thread_id=tid,
            cse_chat_url=_URL_A,
            cse_registration_id="reg-a",
        )
        second = associate_cse(
            thread_id=tid,
            cse_chat_url=_URL_B,
            cse_registration_id="reg-b",
            bound_by=HOP_SEATED_BOUND_BY,
        )
    assert first and second
    unchanged = associate_cse(
        thread_id=tid,
        cse_chat_url=_URL_B,
        cse_registration_id="reg-b",
    )
    assert unchanged is None
    cur = bus_client.get(f"/threads/{tid}/seat").json()["current"]
    assert cur["state"] == "open"
    assert cur["chat_url"] == _URL_B


def test_drift_giw_probe_unavailable_no_crash(
    bus_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = _lane(bus_client, "seat-giw-fail")
    bus_client.post(
        f"/threads/{tid}/seat",
        json={
            "transition": "birth",
            "chat_url": _URL_A,
            "actor": "test",
            "request_id": "giw-birth",
        },
    )
    monkeypatch.setattr(
        "agent_bus_store.db.seat_drift_probes.fetch_giw_holders_map",
        lambda *_a, **_k: ("unavailable", {}),
    )
    summary = bus_client.get("/seats/drift").json()
    assert summary["giw_probe"] == "unavailable"
    assert summary["giw_holder_mismatch_count"] == 0
    assert summary["giw_holder_absent_count"] == 0


def test_drift_giw_holder_absent_when_no_lane_holder(bus_client: TestClient) -> None:
    tid = _lane(bus_client, "seat-giw-absent")
    bus_client.post(
        f"/threads/{tid}/seat",
        json={
            "transition": "birth",
            "chat_url": _URL_A,
            "actor": "test",
            "request_id": "giw-absent-birth",
        },
    )
    with patch(
        "bus_watch.roster.fetch_giw_active_work_payload",
        return_value={"cse_holders": []},
    ):
        summary = seat_drift_summary()
    assert summary["giw_probe"] == "ok"
    assert summary["giw_holder_mismatch_count"] == 0
    assert summary["giw_holder_absent_count"] == 1


def test_drift_giw_mismatch_when_holder_differs(bus_client: TestClient) -> None:
    tid = _lane(bus_client, "seat-giw-mismatch")
    bus_client.post(
        f"/threads/{tid}/seat",
        json={
            "transition": "birth",
            "chat_url": _URL_A,
            "actor": "test",
            "request_id": "giw-mismatch-birth",
        },
    )
    holder = {"chat_url": _URL_B, "registration_id": "reg-b"}
    with patch(
        "agent_bus_store.db.seat_drift_probes.fetch_giw_holders_map",
        lambda *_a, **_k: ("ok", {tid: holder}),
    ):
        summary = seat_drift_summary()
    assert summary["giw_probe"] == "ok"
    assert summary["giw_holder_mismatch_count"] == 1
    assert summary["giw_holder_absent_count"] == 0


def test_drift_registry_probe_fail_open(bus_client: TestClient) -> None:
    tid = _lane(bus_client, "seat-reg-fail")
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        associate_cse(
            thread_id=tid,
            cse_chat_url=_URL_A,
            cse_registration_id="reg-a",
        )
    with patch(
        "agent_bus_store.db.seat_drift_probes.probe_registry_chat",
        lambda *_a, **_k: ("unavailable", None, None),
    ):
        summary = bus_client.get("/seats/drift")
    assert summary.status_code == 200
    assert summary.json()["registry_probe"] == "unavailable"


def test_drift_registry_mismatch(bus_client: TestClient) -> None:
    tid = _lane(bus_client, "seat-reg-mismatch")
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        associate_cse(
            thread_id=tid,
            cse_chat_url=_URL_A,
            cse_registration_id="reg-a",
        )
    with patch(
        "agent_bus_store.db.seat_drift_probes.probe_registry_chat",
        lambda *_a, **_k: ("ok", _URL_B, "cse_01RtRztEY8bgocgT1geurtRx"),
    ):
        summary = seat_drift_summary()
    assert summary["registry_mismatch_count"] == 1


def test_seats_drift_cli_matches_route(
    bus_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    db_path = tmp_path / "cli.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    lines = seat_drift_cli_lines()
    assert any(line.startswith("cse_association_mismatch_count=") for line in lines)
    assert any(line.startswith("giw_probe=") for line in lines)
    assert any(line.startswith("ledger_missing_count=") for line in lines)


def test_seats_drift_counts_lane_with_cse_or_giw_holder_and_no_open_ledger_seat(
    bus_client: TestClient,
) -> None:
    tid_open = _lane(bus_client, "drift-open")
    tid_cse_closed = _lane(bus_client, "drift-cse-closed")
    tid_giw_only = _lane(bus_client, "drift-giw-only")

    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        associate_cse(
            thread_id=tid_open,
            cse_chat_url=_URL_A,
            cse_registration_id="reg-open",
        )
        associate_cse(
            thread_id=tid_cse_closed,
            cse_chat_url=_URL_B,
            cse_registration_id="reg-closed",
        )

    close_thread(tid_cse_closed, mark_all_read=True)

    giw_map = {
        tid_giw_only: {"chat_url": _URL_A, "registration_id": "reg-giw"},
        tid_open: {"chat_url": _URL_A, "registration_id": "reg-open"},
    }

    with patch(
        "agent_bus_store.db.seat_drift_probes.fetch_giw_holders_map",
        return_value=("ok", giw_map),
    ):
        summary = seat_drift_summary()

    assert summary["ledger_missing_count"] == 1
    assert summary["open_seat_count"] >= 1


def _active_work_cse_payload(
    *,
    lane_a: str,
    lane_b: str,
    driving_url: str = _URL_A,
    dormant_url: str = _URL_B,
    cursor_lane: str,
) -> dict:
    return {
        "active_count": 2,
        "active_ops": {
            cursor_lane: {
                "op": "cursor_sdk_generate",
                "thread_id": cursor_lane,
                "holder_lane": cursor_lane,
            }
        },
        "cse_holders": [
            {
                "kind": "cowork_cse",
                "lane_thread_id": lane_a,
                "seat_state": "dormant",
                "chat_url": dormant_url,
                "bound_at": "2026-01-01T00:00:00Z",
            },
            {
                "kind": "cowork_cse",
                "lane_thread_id": lane_a,
                "seat_state": "driving",
                "chat_url": driving_url,
                "registration_id": "reg-driving",
                "bound_at": "2026-01-02T00:00:00Z",
            },
            {
                "kind": "cowork_cse",
                "lane_thread_id": lane_b,
                "seat_state": "driving",
                "chat_url": driving_url,
                "registration_id": "reg-b",
                "bound_at": "2026-01-03T00:00:00Z",
            },
        ],
        "running": [],
    }


def test_fetch_giw_holders_map_from_cse_holders_only(bus_client: TestClient) -> None:
    lane_a = _lane(bus_client, "giw-map-a")
    lane_b = _lane(bus_client, "giw-map-b")
    cursor_lane = _lane(bus_client, "giw-map-cursor")
    body = _active_work_cse_payload(lane_a=lane_a, lane_b=lane_b, cursor_lane=cursor_lane)
    with patch(
        "bus_watch.roster.fetch_giw_active_work_payload",
        return_value=body,
    ):
        from agent_bus_store.db.seat_drift_probes import fetch_giw_holders_map

        status, by_lane = fetch_giw_holders_map()
    assert status == "ok"
    assert cursor_lane not in by_lane
    assert by_lane[lane_a]["chat_url"] == _URL_A
    assert by_lane[lane_b]["chat_url"] == _URL_A


@pytest.mark.parametrize("bad_payload", [{"cse_holders": "not-a-list"}, {}])
def test_fetch_giw_holders_map_unavailable_when_cse_holders_bad(
    bus_client: TestClient, bad_payload: dict
) -> None:
    with patch(
        "bus_watch.roster.fetch_giw_active_work_payload",
        return_value=bad_payload,
    ):
        from agent_bus_store.db.seat_drift_probes import fetch_giw_holders_map

        status, by_lane = fetch_giw_holders_map()
    assert status == "unavailable"
    assert by_lane == {}


def test_giw_holder_chat_skips_per_seat_registry_when_active_load_failed(
    bus_client: TestClient,
) -> None:
    from agent_bus_store.db.seat_drift_probes import giw_holder_chat

    chat_url_calls = 0

    def _counting_chat_url(*_a, **_k):
        nonlocal chat_url_calls
        chat_url_calls += 1
        return _URL_A

    with patch(
        "agent_bus_store.db.seat_drift_probes.chat_url_for_registration",
        side_effect=_counting_chat_url,
    ):
        url, chat_id = giw_holder_chat(
            {"registration_id": "reg-only"},
            registry_active=None,
        )
    assert chat_url_calls == 0
    assert url is None
    assert chat_id is None


def test_drift_giw_zero_when_active_work_matches_driving_holder(
    bus_client: TestClient,
) -> None:
    lane_a = _lane(bus_client, "giw-agree-a")
    lane_b = _lane(bus_client, "giw-agree-b")
    cursor_lane = _lane(bus_client, "giw-agree-cursor")
    bus_client.post(
        f"/threads/{lane_a}/seat",
        json={
            "transition": "birth",
            "chat_url": _URL_A,
            "actor": "test",
            "request_id": "giw-agree-birth-a",
        },
    )
    bus_client.post(
        f"/threads/{lane_b}/seat",
        json={
            "transition": "birth",
            "chat_url": _URL_B,
            "actor": "test",
            "request_id": "giw-agree-birth-b",
        },
    )
    body = _active_work_cse_payload(
        lane_a=lane_a,
        lane_b=lane_b,
        driving_url=_URL_A,
        dormant_url=_URL_B,
        cursor_lane=cursor_lane,
    )
    body["cse_holders"][-1]["chat_url"] = _URL_B
    with patch(
        "bus_watch.roster.fetch_giw_active_work_payload",
        return_value=body,
    ):
        summary = seat_drift_summary()
    assert summary["giw_holder_mismatch_count"] == 0

    body["cse_holders"][1]["chat_url"] = _URL_B
    with patch(
        "bus_watch.roster.fetch_giw_active_work_payload",
        return_value=body,
    ):
        mismatch = seat_drift_summary()
    assert mismatch["giw_holder_mismatch_count"] == 1


def test_closed_lane_not_in_ledger_missing_count(bus_client: TestClient) -> None:
    tid = _lane(bus_client, "ledger-missing-closed")
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        associate_cse(
            thread_id=tid,
            cse_chat_url=_URL_A,
            cse_registration_id="reg-closed-missing",
        )
    close_thread(tid, mark_all_read=True)
    summary = seat_drift_summary()
    assert summary["ledger_missing_count"] == 0


def test_open_lane_association_without_ledger_seat_still_missing(
    bus_client: TestClient,
) -> None:
    tid = _lane(bus_client, "ledger-missing-open-no-seat")
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        associate_cse(
            thread_id=tid,
            cse_chat_url=_URL_A,
            cse_registration_id="reg-open-no-seat",
        )
    cur = current_seat(thread_id=tid)
    transition_seat(
        thread_id=tid,
        transition="close",
        expected_epoch=int(cur["epoch"]),
        actor="test",
        request_id="close-open-no-ledger-seat",
    )
    summary = seat_drift_summary()
    assert summary["ledger_missing_count"] == 1


def test_reopen_idempotent_first_turn_fires_shadow_ledger_hook(
    bus_client: TestClient,
) -> None:
    tid = _lane(bus_client, "reopen-idem-hook")
    insert_turn(
        thread=tid,
        from_agent="cursor-auto",
        to_agent="web-anthropic",
        subject="seed",
        body="turn one",
        idempotent_first_turn=True,
    )
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        associate_cse(
            thread_id=tid,
            cse_chat_url=_URL_A,
            cse_registration_id="reg-idem",
        )
    close_thread(tid, mark_all_read=True)
    assert current_seat(thread_id=tid)["state"] == "closed"

    insert_turn(
        thread=tid,
        from_agent="cursor-auto",
        to_agent="web-anthropic",
        subject="seed",
        body="turn one",
        idempotent_first_turn=True,
    )
    reopened = current_seat(thread_id=tid)
    assert reopened["state"] == "open"
    assert reopened["chat_url"] == _URL_A


def test_drift_agreement_all_probes_zero(bus_client: TestClient) -> None:
    open_tid = _lane(bus_client, "agree-open")
    closed_tid = _lane(bus_client, "agree-closed")
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        associate_cse(
            thread_id=open_tid,
            cse_chat_url=_URL_A,
            cse_registration_id="reg-agree-open",
        )
        associate_cse(
            thread_id=closed_tid,
            cse_chat_url=_URL_B,
            cse_registration_id="reg-agree-closed",
        )
    close_thread(closed_tid, mark_all_read=True)

    active = {
        "reg-agree-open": {"chat_url": _URL_A},
    }
    body = _active_work_cse_payload(
        lane_a=open_tid,
        lane_b="999999",
        driving_url=_URL_A,
        dormant_url=_URL_B,
        cursor_lane=open_tid,
    )
    body["cse_holders"] = [
        h for h in body["cse_holders"] if h.get("lane_thread_id") == open_tid
    ]

    with (
        patch(
            "bus_watch.roster.fetch_giw_active_work_payload",
            return_value=body,
        ),
        patch(
            "agent_bus_store.db.seat_drift_probes.load_registry_active",
            return_value=active,
        ),
    ):
        summary = seat_drift_summary()

    assert summary["giw_holder_mismatch_count"] == 0
    assert summary["giw_holder_absent_count"] == 0
    assert summary["ledger_missing_count"] == 0
    assert summary["cse_association_mismatch_count"] == 0
    assert summary["registry_mismatch_count"] == 0


def test_reopen_closed_thread_restores_or_births_open_ledger_seat(
    bus_client: TestClient,
) -> None:
    tid = _lane(bus_client, "reopen-ledger")
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        associate_cse(
            thread_id=tid,
            cse_chat_url=_URL_A,
            cse_registration_id="reg-reopen",
        )
    assert current_seat(thread_id=tid)["state"] == "open"

    close_thread(tid, mark_all_read=True)
    assert current_seat(thread_id=tid)["state"] == "closed"

    _errors_before = shadow_hook_error_count()
    insert_turn(
        thread=tid,
        from_agent="cursor-auto",
        to_agent="web-anthropic",
        subject="reply",
        body="reopen via reply",
    )
    reopened = current_seat(thread_id=tid)
    assert reopened["state"] == "open"
    assert reopened["chat_url"] == _URL_A
    assert shadow_hook_error_count() == _errors_before

    with connect() as conn:
        holder = _holder_of_chat(conn, chat_id="cse_01CodB7tom1281iY8BmZJcZM")
    assert holder is not None
    assert str(holder["thread_id"]) == tid

    hop = transition_seat(
        thread_id=tid,
        transition="hop",
        chat_url=_URL_B,
        expected_epoch=int(reopened["epoch"]),
        actor="test",
        request_id="reopen-hop",
    )
    assert hop["state"] == "open"


def test_watchdog_close_closes_ledger_seat_and_releases_chat_hold(
    bus_client: TestClient,
) -> None:
    thread_row, *_ = create_thread_with_turn(
        slug="watchdog-ledger",
        from_agent="cursor-auto",
        to_agent="web-anthropic",
        subject="work",
        body="seed",
        lifecycle_state="active",
    )
    tid = thread_row["id"]
    admit_dispatch(
        thread_id=tid,
        execution_id="exec-watchdog-ledger",
        pipeline_id="cursor-sdk-generate",
        caller_agent="cursor-auto",
    )
    transition_seat(
        thread_id=tid,
        transition="birth",
        chat_url=_URL_A,
        actor="test",
        request_id="watchdog-birth",
    )
    chat_id = "cse_01CodB7tom1281iY8BmZJcZM"
    with connect() as conn:
        assert _holder_of_chat(conn, chat_id=chat_id) is not None

    stale = (datetime.now(UTC) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with connect() as conn:
        conn.execute(
            "UPDATE threads SET updated_at = ? WHERE id = ?",
            (stale, tid),
        )

    assert _reap_single(
        tid,
        expected_state="active",
        reason="all_terminal_no_delivery",
        stale_before=now(),
    )

    assert current_seat(thread_id=tid)["state"] == "closed"
    with connect() as conn:
        assert _holder_of_chat(conn, chat_id=chat_id) is None


def test_seats_drift_fetches_giw_holders_once_and_loads_registry_once(
    bus_client: TestClient,
) -> None:
    assert not inspect.iscoroutinefunction(seat_routes.seats_drift_route)

    lane_urls = [_URL_A, _URL_B, _URL_C]
    tids: list[str] = []
    for i, chat_url in enumerate(lane_urls):
        tid = _lane(bus_client, f"drift-batch-{i}")
        tids.append(tid)
        birth = bus_client.post(
            f"/threads/{tid}/seat",
            json={
                "transition": "birth",
                "chat_url": chat_url,
                "actor": "test",
                "request_id": f"batch-birth-{tid}",
            },
        )
        assert 200 <= birth.status_code < 300, birth.text

    reg_only_tid = tids[1]
    reg_only_url = lane_urls[1]

    giw_payload_calls = 0
    registry_loads = 0

    def _active_work_payload():
        nonlocal giw_payload_calls
        giw_payload_calls += 1
        return {
            "cse_holders": [
                {
                    "kind": "cowork_cse",
                    "lane_thread_id": tids[0],
                    "seat_state": "driving",
                    "chat_url": lane_urls[0],
                    "registration_id": f"reg-{tids[0]}",
                },
                {
                    "kind": "cowork_cse",
                    "lane_thread_id": reg_only_tid,
                    "seat_state": "driving",
                    "registration_id": f"reg-{reg_only_tid}",
                },
                {
                    "kind": "cowork_cse",
                    "lane_thread_id": tids[2],
                    "seat_state": "driving",
                    "chat_url": lane_urls[2],
                    "registration_id": f"reg-{tids[2]}",
                },
            ]
        }

    def _fake_registry():
        nonlocal registry_loads
        registry_loads += 1
        return {
            f"reg-{tid}": {"chat_url": url}
            for tid, url in zip(tids, lane_urls, strict=True)
        }

    chat_url_loads = 0

    def _counting_chat_url(*_a, **_k):
        nonlocal chat_url_loads
        chat_url_loads += 1
        return reg_only_url

    giw_calls = 0

    def _counting_giw_fetch(*_a, **_k):
        nonlocal giw_calls
        giw_calls += 1
        return _real_fetch_giw()

    with (
        patch(
            "bus_watch.roster.fetch_giw_active_work_payload",
            side_effect=_active_work_payload,
        ),
        patch(
            "agent_bus_store.db.seat_drift_probes.fetch_giw_holders_map",
            side_effect=_counting_giw_fetch,
        ),
        patch(
            "agent_bus_store.db.seat_drift_probes.load_registry_active",
            side_effect=_fake_registry,
        ),
        patch(
            "agent_bus_store.db.seat_drift_probes.chat_url_for_registration",
            side_effect=_counting_chat_url,
        ),
    ):
        resp = bus_client.get("/seats/drift")

    open_seats = bus_client.get("/seats", params={"state": "open"}).json()["seats"]
    open_ids = {str(row["thread_id"]) for row in open_seats}
    assert reg_only_tid in open_ids

    assert resp.status_code == 200
    assert giw_payload_calls == 1
    assert giw_calls == 1
    assert registry_loads == 1
    assert chat_url_loads == 0
    body = resp.json()
    assert body["giw_holder_mismatch_count"] == 0
    assert body["giw_holder_absent_count"] == 0
