"""Per-slice gate and args validation for ``cursor_glass`` admits."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from systems.edge_ops import config, runtime
from systems.edge_ops.auth import require_internal_caller
from systems.edge_ops.model import CODE_ARGS_INVALID, CODE_KIND_DISABLED
from systems.edge_ops.routes import router

from .conftest import seen_edge


@pytest.fixture
def client(journal, monkeypatch):
    monkeypatch.setenv(config.ENABLED_ENV_KEY, "true")
    runtime.set_default_journal(journal)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_internal_caller] = lambda: {"client_id": "test"}
    with TestClient(app) as test_client:
        yield test_client
    runtime.set_default_journal(None)


@pytest.mark.asyncio
async def test_admit_cursor_glass_kind_disabled(journal) -> None:
    await seen_edge(journal, kinds=("cursor_glass",))

    from systems.edge_ops.kinds.cursor_glass import validate_cursor_glass_admit
    from systems.edge_ops.model import EdgeOpRefusedError

    with pytest.raises(EdgeOpRefusedError) as refusal:
        validate_cursor_glass_admit(
            op="wake_tab",
            args={"gui_host": ":8", "body": "x"},
        )
    assert refusal.value.code == CODE_KIND_DISABLED


@pytest.mark.asyncio
async def test_admit_http_rejects_kind_disabled(client, journal) -> None:
    await seen_edge(journal, kinds=("cursor_glass",))
    response = client.post(
        "/edge/ops",
        json={
            "kind": "cursor_glass",
            "op": "wake_tab",
            "idempotency_key": "k1",
            "args": {"gui_host": ":8", "body": "wake"},
        },
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == CODE_KIND_DISABLED


@pytest.mark.asyncio
async def test_admit_http_rejects_missing_body_when_gate_on(
    client, journal, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(config.CURSOR_GLASS_ENV_KEY, "true")
    await seen_edge(journal, kinds=("cursor_glass",))
    response = client.post(
        "/edge/ops",
        json={
            "kind": "cursor_glass",
            "op": "wake_tab",
            "idempotency_key": "k2",
            "args": {"gui_host": ":8"},
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == CODE_ARGS_INVALID
