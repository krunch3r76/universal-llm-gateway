"""op=spawn wire validation on the Stargate team_dispatch route (21088#104 R1)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline


def _client() -> TestClient:
    root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "services" / "universal-stargate"))
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError

    from systems.frontier_consult.route import team_router
    from systems.proxy.app import validation_exception_handler

    surface = FastAPI()
    surface.include_router(team_router)
    surface.add_exception_handler(RequestValidationError, validation_exception_handler)
    return TestClient(surface)


def _spawn_body(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "op": "spawn",
        "backend": "cursor_ide",
        "gui_host": ":8",
        "thread": "thread-house-1",
        "spawn_purpose": "one line purpose",
        "prompt": "Do the work.",
    }
    base.update(overrides)
    return base


def _post(client: TestClient, body: dict[str, object]):
    proxy = MagicMock(event_bus=None)
    with patch("systems.proxy.dependencies.get_proxy", return_value=proxy):
        return client.post("/api/v1/team/dispatch", json=body)


def _assert_422(response, *, code: str, field: str) -> None:
    assert response.status_code == 422, response.text
    payload = response.json()
    assert payload["error"]["code"] == code, payload
    assert payload.get("field") == field, payload


def test_op_spawn_requires_spawn_purpose() -> None:
    client = _client()
    for bad in ({"spawn_purpose": None}, {"spawn_purpose": ""}, {"spawn_purpose": "a\nb"}):
        resp = _post(client, _spawn_body(**bad))
        _assert_422(resp, code="spawn_purpose_required", field="spawn_purpose")


def test_op_spawn_requires_thread() -> None:
    client = _client()
    body = _spawn_body()
    del body["thread"]
    resp = _post(client, body)
    _assert_422(resp, code="spawn_thread_required", field="thread")


def test_op_spawn_rejects_non_ide_backend() -> None:
    client = _client()
    resp = _post(client, _spawn_body(backend="cursor_sdk"))
    _assert_422(resp, code="spawn_backend_unsupported", field="backend")


def test_op_spawn_cursor_ide_prepends_prefix_before_relay() -> None:
    """R4: route prepends ledger_row + purpose lines to prompt before relay_spawn_tab."""
    client = _client()
    captured: dict[str, str] = {}

    def _relay(**kwargs: object) -> dict[str, object]:
        captured["prompt"] = str(kwargs.get("prompt") or "")
        return {"ok": True, "agent_id": "agent-test"}

    proxy = MagicMock(event_bus=None)
    with (
        patch("systems.proxy.dependencies.get_proxy", return_value=proxy),
        patch(
            "cursor_bridge.bridge_spawn_relay.relay_spawn_tab",
            side_effect=_relay,
        ),
    ):
        resp = client.post("/api/v1/team/dispatch", json=_spawn_body())

    assert resp.status_code == 200, resp.text
    prompt = captured.get("prompt") or ""
    assert prompt.startswith("ledger_row: ")
    assert "\npurpose: one line purpose\n" in prompt
    assert prompt.endswith("Do the work.")


def test_ide_launch_error_marks_failed_after_spine(tmp_path: Path) -> None:
    client = _client()

    def _relay_fail(**kwargs: object) -> dict[str, object]:
        return {"ok": False, "error": "no_page"}

    proxy = MagicMock(event_bus=None)
    with (
        patch("systems.proxy.dependencies.get_proxy", return_value=proxy),
        patch(
            "cursor_bridge.bridge_spawn_relay.relay_spawn_tab",
            side_effect=_relay_fail,
        ),
        patch(
            "cursor_bridge.spawn_endpoint_house_ledger.ledger_path",
            return_value=tmp_path / "cursor-bridge-tab-wake-ledger.jsonl",
        ),
    ):
        resp = client.post("/api/v1/team/dispatch", json=_spawn_body())
    assert resp.status_code == 502
    from cursor_bridge.spawn_endpoint_house_ledger import ledger_path, read_all_rows

    rows = read_all_rows(ledger_path(tmp_path))
    spawns = [r for r in rows if r.get("kind") == "spawn"]
    assert len(spawns) == 1
    assert spawns[0]["state"] == "failed"


def test_ide_spawn_row_launching_before_relay(tmp_path: Path) -> None:
    client = _client()
    order: list[str] = []

    def _relay(**kwargs: object) -> dict[str, object]:
        order.append("relay")
        return {"ok": True, "editor_agent_id": "agent-99"}

    def _record_spawn(**kwargs: object) -> dict[str, object]:
        order.append("spawn")
        return {"ok": True, "row_id": "a" * 32}

    proxy = MagicMock(event_bus=None)
    with (
        patch("systems.proxy.dependencies.get_proxy", return_value=proxy),
        patch(
            "cursor_bridge.bridge_spawn_relay.relay_spawn_tab",
            side_effect=_relay,
        ),
        patch(
            "cursor_bridge.spawn_endpoint_house_ledger.record_spawn",
            side_effect=_record_spawn,
        ),
        patch("cursor_bridge.spawn_endpoint_house_ledger.patch_spawn_row"),
    ):
        resp = client.post("/api/v1/team/dispatch", json=_spawn_body())
    assert resp.status_code == 200
    assert order == ["spawn", "relay"]
    assert order.index("spawn") < order.index("relay")


def test_op_ledger_house_trims_to_5120(tmp_path: Path) -> None:
    from cursor_bridge.spawn_endpoint_house_ledger import record_spawn

    for i in range(20):
        record_spawn(
            surface="cursor_ide",
            thread="house-thread",
            purpose=f"p{i}",
            purpose_unstated=False,
            watch_dir=tmp_path,
        )
    client = _client()
    proxy = MagicMock(event_bus=None)
    with (
        patch("systems.proxy.dependencies.get_proxy", return_value=proxy),
        patch(
            "cursor_bridge.spawn_endpoint_house_ledger.ledger_path",
            return_value=tmp_path / "cursor-bridge-tab-wake-ledger.jsonl",
        ),
    ):
        resp = client.post(
            "/api/v1/team/dispatch",
            json={"op": "ledger", "house": "house-thread", "limit": 10},
        )
    assert resp.status_code == 200
    assert len(resp.content) <= 5120


def test_op_ledger_work_key_unchanged() -> None:
    client = _client()
    proxy = MagicMock(event_bus=None)
    with (
        patch("systems.proxy.dependencies.get_proxy", return_value=proxy),
        patch(
            "systems.frontier_consult.cursor_sdk_claim_dispatch.relay_house_claim",
            new_callable=AsyncMock,
        ) as relay,
    ):
        relay.return_value = (True, {"status_code": 200, "rows": []})
        resp = client.post(
            "/api/v1/team/dispatch",
            json={"op": "ledger", "work_key": "todo:foo", "limit": 5},
        )
    relay.assert_called_once()
    assert resp.status_code == 200
