"""GET /v1/grok/window-routing host owner route."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cdp_ask.app import create_app
from cursor_bridge import grok_window_bindings as bindings
from cursor_bridge.grok_window_bindings import write_routing

pytestmark = pytest.mark.offline


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    routing_path = tmp_path / "grok-window-routing.json"
    monkeypatch.setattr(bindings, "state_path", lambda: routing_path)
    return TestClient(create_app())


def test_get_grok_window_routing_ok(client: TestClient) -> None:
    write_routing(
        default_window="9",
        insyncerator_window="10",
        source="route-test",
        updated_by="pytest",
    )
    resp = client.get("/v1/grok/window-routing")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    epoch = body["provenance"]["epoch"]
    assert body["default_window"]["display_scope"].endswith("/default_window")
    assert body["grok_bot_home"]["display"] == body["default_window"]["display"]
    assert body["provenance"]["epoch"] == epoch