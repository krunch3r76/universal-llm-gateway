"""GET /delivery-bindings/resolve — wake and steer delivery actuation."""

from __future__ import annotations

import json

import pytest
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, init_db
from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint
from agent_bus_store.server import create_app
from fastapi.testclient import TestClient

from services.git_integration_worker.cursor_dispatch_ledger import (
    CURSOR_SDK_DISPATCH_LEDGER_ENV,
    CursorDispatchLedger,
)

_ORION_AGENT_ID = "4ed020f2-f0b2-4f3d-b49c-89aa34930f19"
_HOUSE_ID = "20995"
_MAESTRO_HOUSE = "12286"
_21220_AGENT_ID = "723224a2-ea2a-5b32-a19c-70e2ae09430e"
_21220_HOUSE = "21220"
_ORION_HOUSE = "21207"
_PINNED_CONVERSATION_ID = "6ae2e1bc-0c94-83e8-9f21-62467c4935d5"


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    ledger_path = tmp_path / "cursor-sdk-dispatch.db"
    monkeypatch.setenv(CURSOR_SDK_DISPATCH_LEDGER_ENV, str(ledger_path))
    CursorDispatchLedger._instance = None
    init_db()
    CursorDispatchLedger.instance()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def _insert_dispatch_row(
    *,
    dispatch_id: str,
    thread_id: str,
    status: str,
    execution_id: str = "exec-1",
    resume_of: str | None = None,
) -> None:
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        conn.execute(
            """
            INSERT INTO cursor_sdk_dispatches (
                dispatch_id, fingerprint, thread_id, execution_id, resolved_model,
                message_present, status, record_json, resume_of
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                dispatch_id,
                f"fp-{dispatch_id}",
                thread_id,
                execution_id,
                "composer-2.5",
                status,
                json.dumps({}),
                resume_of,
            ),
        )


def test_delivery_bindings_resolve_wake_21220_cursor_glass(client: TestClient) -> None:
    """21220 wake binds cursor Glass :8 tab per 21207#51."""
    assert create_thread(thread_id=_21220_HOUSE, slug="mediator") is not None
    subscribe_endpoint(
        agent="cursor",
        thread_scope=_21220_HOUSE,
        surface="cursor",
        display=":8",
        title="20995 delivery-bindings",
        agent_id=_21220_AGENT_ID,
    )
    resp = client.get(
        "/delivery-bindings/resolve",
        params={"house_id": _21220_HOUSE, "intent": "wake"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "bound"
    actuation = body["current"]["actuation"]
    assert actuation["type"] == "cursor_glass"
    assert actuation["gui_host"] == ":8"
    assert actuation["agent_id"] == _21220_AGENT_ID
    assert actuation["cdp_base"] == "http://127.0.0.1:9338"


def test_delivery_bindings_resolve_wake_21207_orion_chat(client: TestClient) -> None:
    """21207 to=orion resolves orion_chat actuation (pinned conversation)."""
    resp = client.get(
        "/delivery-bindings/resolve",
        params={"house_id": _ORION_HOUSE, "intent": "wake"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "bound"
    actuation = body["current"]["actuation"]
    assert actuation["type"] == "orion_chat"
    assert actuation["conversation_id"] == _PINNED_CONVERSATION_ID


def test_delivery_bindings_resolve_wake_unbound_unknown_house(client: TestClient) -> None:
    resp = client.get(
        "/delivery-bindings/resolve",
        params={"house_id": "99999", "intent": "wake"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "unbound"
    assert body["current"] is None
    assert body["reason"] == "unbound"


def test_delivery_bindings_resolve_wake_orion_cursor_glass(client: TestClient) -> None:
    """Case 1: house 20995 wake resolves cursor Glass actuation from subscription row."""
    assert create_thread(thread_id=_HOUSE_ID, slug="orion-dispatch") is not None
    subscribe_endpoint(
        agent="cursor",
        thread_scope=_HOUSE_ID,
        surface="cursor",
        display="orion-node",
        title="orion-node",
        agent_id=_ORION_AGENT_ID,
    )
    resp = client.get(
        "/delivery-bindings/resolve",
        params={"house_id": _HOUSE_ID, "intent": "wake"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in ("state", "as_of", "basis", "source", "scope", "current", "stale"):
        assert key in body, body
    assert body["state"] == "bound"
    assert body["stale"] is False
    assert body["scope"] == f"house:{_HOUSE_ID}/intent:wake"
    actuation = body["current"]["actuation"]
    assert actuation["type"] == "cursor_glass"
    assert actuation["gui_host"] == "orion-node"
    assert actuation["agent_id"] == _ORION_AGENT_ID
    assert actuation["cdp_base"] == "http://127.0.0.1:19338"


def test_delivery_bindings_resolve_steer_active_resume_of(client: TestClient) -> None:
    """Case 3: active dispatch with resume_of → cursor_sdk_dispatch / dispatch_ledger_active."""
    _insert_dispatch_row(
        dispatch_id="disp-steer-live",
        thread_id=_HOUSE_ID,
        status="running",
        execution_id="exec-steer-live",
        resume_of="parent-exec",
    )
    resp = client.get(
        "/delivery-bindings/resolve",
        params={"house_id": _HOUSE_ID, "intent": "steer"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "bound"
    assert body["basis"] == "dispatch_ledger_active"
    actuation = body["current"]["actuation"]
    assert actuation["type"] == "cursor_sdk_dispatch"
    assert actuation["dispatch_id"] == "disp-steer-live"
    assert actuation["execution_id"] == "exec-steer-live"
    assert actuation["resume_of"] == "parent-exec"


def test_delivery_bindings_resolve_steer_terminal_does_not_beat_subscription(
    client: TestClient,
) -> None:
    """Terminal ledger row must not win over a live cursor UI subscription."""
    assert create_thread(thread_id=_HOUSE_ID, slug="orion-dispatch") is not None
    subscribe_endpoint(
        agent="cursor",
        thread_scope=_HOUSE_ID,
        surface="cursor",
        display="orion-node",
        title="orion-node",
        agent_id=_ORION_AGENT_ID,
    )
    _insert_dispatch_row(
        dispatch_id="disp-terminal-only",
        thread_id=_HOUSE_ID,
        status="completed",
        execution_id="exec-terminal",
        resume_of="parent-done",
    )
    resp = client.get(
        "/delivery-bindings/resolve",
        params={"house_id": _HOUSE_ID, "intent": "steer"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "unbound"
    current = body.get("current")
    assert current is None or current.get("actuation", {}).get("dispatch_id") != (
        "disp-terminal-only"
    )


def _cdp_registry_seat_row(
    registration_id: str,
    *,
    parent_thread: str,
    seat_bound_at: float | None,
    seat_closed_at: float | None = None,
    seat_lane: str = _MAESTRO_HOUSE,
) -> dict[str, object]:
    return {
        "registration_id": registration_id,
        "port": 9222,
        "profile_suffix": registration_id,
        "holder": "holder",
        "status": "active",
        "parent_thread": parent_thread,
        "purpose": "operator-proxy",
        "seat_lane": seat_lane,
        "seat_bound_at": seat_bound_at,
        "seat_closed_at": seat_closed_at,
        "started_at": seat_bound_at or 0.0,
        "chat_url": f"https://claude.ai/cowork/cse_{registration_id}",
    }


def _patch_inform_ask_owner_seat_rows(
    monkeypatch: pytest.MonkeyPatch,
    active: dict[str, dict[str, object]],
) -> None:
    from claude_bundles.hop_cadence_seat_snap import seat_rows_from_registry_records

    def _read() -> tuple[list[dict[str, object]], None]:
        return seat_rows_from_registry_records(active), None

    monkeypatch.setattr(
        "agent_bus_store.db.endpoint_wake_ledger._read_inform_seat_rows_from_ask_owner",
        _read,
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.session_address.chat_url_for_registration",
        lambda rid: f"https://claude.ai/cowork/cse_{rid}" if rid else None,
    )


def test_delivery_bindings_resolve_inform_maestro_lane_holder_hops(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Case 2: house 12286 inform follows the current CDP registry lane holder."""
    from claude_bundles.hop_cadence_seat_snap import seat_rows_from_registry_records

    first_lane = "21062"
    second_lane = "21097"
    first_reg = "reg-inform-first-aaaa"
    second_reg = "reg-inform-second-bbbb"

    active: dict[str, dict[str, object]] = {
        first_reg: _cdp_registry_seat_row(
            first_reg,
            parent_thread=first_lane,
            seat_bound_at=1.0,
        ),
    }
    _patch_inform_ask_owner_seat_rows(monkeypatch, active)

    resp = client.get(
        "/delivery-bindings/resolve",
        params={"house_id": _MAESTRO_HOUSE, "intent": "inform"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "bound"
    assert body["basis"] == "cdp_registry_lane_holder"
    assert body["scope"] == f"house:{_MAESTRO_HOUSE}/intent:inform"
    assert body["current"]["actuation"] == {
        "type": "bus_poll",
        "thread_id": first_lane,
    }
    assert body["current"]["registration_id"] == first_reg
    assert body["current"]["chat_url"] == (
        f"https://claude.ai/cowork/cse_{first_reg}"
    )

    active[first_reg] = _cdp_registry_seat_row(
        first_reg,
        parent_thread=first_lane,
        seat_bound_at=1.0,
        seat_closed_at=2.0,
    )
    active[second_reg] = _cdp_registry_seat_row(
        second_reg,
        parent_thread=second_lane,
        seat_bound_at=3.0,
    )
    closed_rows = seat_rows_from_registry_records(active)
    assert all(
        row.get("registration_id") != first_reg for row in closed_rows
    ), "closed predecessor must not appear in seat_rows projection"

    resp2 = client.get(
        "/delivery-bindings/resolve",
        params={"house_id": _MAESTRO_HOUSE, "intent": "inform"},
    )
    assert resp2.status_code == 200, resp2.text
    body2 = resp2.json()
    assert body2["state"] == "bound"
    assert body2["current"]["actuation"]["thread_id"] == second_lane
    assert body2["current"]["actuation"]["thread_id"] != first_lane
    assert body2["current"]["registration_id"] == second_reg
    assert body2["current"]["chat_url"] == (
        f"https://claude.ai/cowork/cse_{second_reg}"
    )


def test_delivery_bindings_resolve_inform_seat_fields_absent(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Open seat row without seat_bound_at → unbound seat_fields_absent."""
    reg = "reg-inform-no-bound"
    active = {
        reg: _cdp_registry_seat_row(
            reg,
            parent_thread="21062",
            seat_bound_at=None,
        ),
    }
    _patch_inform_ask_owner_seat_rows(monkeypatch, active)

    resp = client.get(
        "/delivery-bindings/resolve",
        params={"house_id": _MAESTRO_HOUSE, "intent": "inform"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "unbound"
    current = body["current"]
    assert current is not None
    assert current["reason"] == "seat_fields_absent"
    assert reg in current["candidates"]
    assert "seat_fields_absent" in current["receipt"]


def test_delivery_bindings_resolve_inform_ask_owner_unconfigured(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset PROJECT_ASK_URL → inform unbound with ask_owner_unconfigured."""
    monkeypatch.delenv("PROJECT_ASK_URL", raising=False)
    resp = client.get(
        "/delivery-bindings/resolve",
        params={"house_id": _MAESTRO_HOUSE, "intent": "inform"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "unbound"
    current = body["current"]
    assert current is not None
    assert current["reason"] == "ask_owner_unconfigured"
    assert "ask_owner_unconfigured" in current["receipt"]


def test_delivery_bindings_resolve_inform_seat_ambiguous(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two open rows with equal seat_bound_at → unbound seat_ambiguous."""
    reg_a = "reg-inform-tie-aaaa"
    reg_b = "reg-inform-tie-bbbb"
    active = {
        reg_a: _cdp_registry_seat_row(
            reg_a,
            parent_thread="21062",
            seat_bound_at=5.0,
        ),
        reg_b: _cdp_registry_seat_row(
            reg_b,
            parent_thread="21097",
            seat_bound_at=5.0,
        ),
    }
    _patch_inform_ask_owner_seat_rows(monkeypatch, active)

    resp = client.get(
        "/delivery-bindings/resolve",
        params={"house_id": _MAESTRO_HOUSE, "intent": "inform"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "unbound"
    current = body["current"]
    assert current is not None
    assert current["reason"] == "seat_ambiguous"
    assert set(current["candidates"]) == {reg_a, reg_b}
    assert "seat_ambiguous" in current["receipt"]
