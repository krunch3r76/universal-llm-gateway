"""Unit and HTTP coverage for CapacityPool.get_model_slots and admission/state slots."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_stargate_root = str(Path(__file__).resolve().parents[3])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from systems.proxy.dependencies import get_auth_dependency, get_proxy  # noqa: E402
from systems.proxy.routers.api import admission_state  # noqa: E402
from systems.routing.capacity.pool import CapacityPool  # noqa: E402

pytestmark = pytest.mark.offline

_GATEWAY = "A"
_MODEL = "M"


@pytest.fixture
def pool() -> CapacityPool:
    return CapacityPool(event_bus=None)


def _admission_client(
    pool: CapacityPool,
    monkeypatch: pytest.MonkeyPatch,
    *,
    status_map: dict[str, dict[str, list[str]]] | None = None,
) -> TestClient:
    fed = SimpleNamespace(config=SimpleNamespace(stargate_id="local-stargate"))
    monkeypatch.setattr(admission_state, "get_federation_integration", lambda: fed)
    if status_map is None:
        status_map = {
            _MODEL: {"loaded_on": [], "busy_on": [], "loading_on": []},
        }
    monkeypatch.setattr(
        admission_state,
        "get_model_status_map",
        lambda _local_id, _gm, _fm: status_map,
    )
    proxy = SimpleNamespace(
        capacity_pool=pool,
        gateway_manager=SimpleNamespace(),
        federated_manager=None,
    )
    app = FastAPI()
    app.include_router(admission_state.router, prefix="/api/v1")
    app.dependency_overrides[get_proxy] = lambda: proxy
    app.dependency_overrides[get_auth_dependency] = lambda: {}
    return TestClient(app)


@pytest.mark.asyncio
async def test_get_model_slots_per_gateway_breakdown(pool: CapacityPool) -> None:
    """Pool cap 4 with two in-flight on (A, M) exposes available=2 (acceptance 0.1)."""
    pool.set_capacity(_GATEWAY, _MODEL, 4)
    allowed = frozenset({_GATEWAY})
    t1 = await pool.acquire_token(
        request_id="req-1",
        model_id=_MODEL,
        allowed_gateway_ids=allowed,
    )
    t2 = await pool.acquire_token(
        request_id="req-2",
        model_id=_MODEL,
        allowed_gateway_ids=allowed,
    )
    try:
        slots = pool.get_model_slots(_MODEL)
        assert slots == [
            {
                "gateway_id": _GATEWAY,
                "capacity": 4,
                "in_flight": 2,
                "available": 2,
            }
        ]
    finally:
        await t1.release()
        await t2.release()


def test_admission_state_includes_slots_key(
    pool: CapacityPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/v1/admission/state returns slots list (acceptance 0.2)."""
    pool.set_capacity(_GATEWAY, _MODEL, 4)
    client = _admission_client(pool, monkeypatch)
    response = client.get("/api/v1/admission/state", params={"model_id": _MODEL})
    assert response.status_code == 200
    body = response.json()
    assert "slots" in body
    assert body["slots"] == [
        {
            "gateway_id": _GATEWAY,
            "capacity": 4,
            "in_flight": 0,
            "available": 4,
        }
    ]


def test_admission_state_preserves_existing_field_types(
    pool: CapacityPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing admission fields keep bool/int/null types when slots is added (0.3)."""
    pool.set_capacity(_GATEWAY, _MODEL, 1)
    status_map = {
        _MODEL: {
            "loaded_on": ["gw-loaded"],
            "busy_on": [],
            "loading_on": ["gw-loading"],
        },
    }
    client = _admission_client(pool, monkeypatch, status_map=status_map)
    response = client.get("/api/v1/admission/state", params={"model_id": _MODEL})
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert isinstance(body["paused"], bool)
    assert body["paused_reason"] is None or isinstance(body["paused_reason"], str)
    assert body["paused_until_ms"] is None or isinstance(body["paused_until_ms"], int)
    assert isinstance(body["loading"], bool)
    assert isinstance(body["loaded"], bool)
    assert isinstance(body["queue_depth"], int)
    assert body["loading"] is True
    assert body["loaded"] is True
    assert body["queue_depth"] == 0
