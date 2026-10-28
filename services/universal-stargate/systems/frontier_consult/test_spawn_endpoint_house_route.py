"""op=spawn wire validation on the Stargate team_dispatch route (21088#104 R1)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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
