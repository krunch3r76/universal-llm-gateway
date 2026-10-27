"""GET /delivery-bindings/resolve — wake intent from endpoint subscription rows."""

from __future__ import annotations

import pytest
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, init_db
from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint
from agent_bus_store.server import create_app
from fastapi.testclient import TestClient

_ORION_AGENT_ID = "4ed020f2-f0b2-4f3d-b49c-89aa34930f19"
_HOUSE_ID = "20995"


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


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
