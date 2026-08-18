"""GET /health exports publisher drop counters as a live discriminator."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_bus_store.events import publisher
from agent_bus_store.server import create_app


@pytest.mark.offline
def test_health_exports_drop_counters(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.setattr(publisher, "dropped_enqueue", 3)
    monkeypatch.setattr(publisher, "dropped_send", 1)
    app = create_app(db_path=str(tmp_path / "bus.db"))
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dropped_enqueue"] == 3
    assert body["dropped_send"] == 1
    assert body["status"] == "ok"
    assert "pid" in body
    assert "code_version" in body
    assert isinstance(body["publisher_started_at"], str)
    assert "T" in body["publisher_started_at"]


@pytest.mark.offline
def test_snapshot_drop_counters_always_present() -> None:
    publisher.dropped_enqueue = 0
    publisher.dropped_send = 0
    snap = publisher.snapshot_drop_counters()
    assert snap["dropped_enqueue"] == 0
    assert snap["dropped_send"] == 0
    assert snap["publisher_started_at"] == publisher.publisher_started_at
    assert isinstance(snap["publisher_started_at"], str)
    assert "T" in snap["publisher_started_at"]
