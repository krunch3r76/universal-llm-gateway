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

    cur = current_seat(thread_id=tid_cse_closed)
    transition_seat(
        thread_id=tid_cse_closed,
        transition="close",
        expected_epoch=int(cur["epoch"]),
        actor="test",
        request_id="close-for-missing",
    )

    giw_map = {
        tid_giw_only: {"chat_url": _URL_A, "registration_id": "reg-giw"},
        tid_open: {"chat_url": _URL_A, "registration_id": "reg-open"},
    }

    with patch(
        "agent_bus_store.db.seat_drift_probes.fetch_giw_holders_map",
        return_value=("ok", giw_map),
    ):
        summary = seat_drift_summary()

    assert summary["ledger_missing_count"] == 2
    assert summary["open_seat_count"] >= 1


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

    tids = [_lane(bus_client, f"drift-batch-{i}") for i in range(3)]
    for tid in tids:
        bus_client.post(
            f"/threads/{tid}/seat",
            json={
                "transition": "birth",
                "chat_url": _URL_A,
                "actor": "test",
                "request_id": f"batch-birth-{tid}",
            },
        )

    giw_calls = 0
    registry_loads = 0

    def _fake_giw(*_a, **_k):
        nonlocal giw_calls
        giw_calls += 1
        return (
            "ok",
            {tid: {"chat_url": _URL_A, "registration_id": "reg-x"} for tid in tids},
        )

    def _fake_registry():
        nonlocal registry_loads
        registry_loads += 1
        return {}

    with (
        patch(
            "agent_bus_store.db.seat_drift_probes.fetch_giw_holders_map",
            side_effect=_fake_giw,
        ),
        patch(
            "agent_bus_store.db.seat_drift_probes.load_registry_active",
            side_effect=_fake_registry,
        ),
    ):
        resp = bus_client.get("/seats/drift")

    assert resp.status_code == 200
    assert giw_calls == 1
    assert registry_loads == 1
