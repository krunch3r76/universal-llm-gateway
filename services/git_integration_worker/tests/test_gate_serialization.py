"""Concurrency and OpenAPI tests for git-integration-worker."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

BASE = "http://test"


@pytest.fixture
def app():
    from services.git_integration_worker.app import create_app

    return create_app()


@pytest.fixture(autouse=True)
def _no_events(monkeypatch):
    monkeypatch.setattr(
        "services.git_integration_worker.events.publish_lib_signal",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr("git_integrate.events.record", lambda *_a, **_k: None)


@pytest.mark.asyncio
async def test_integrate_openapi_has_no_green_gate_cmd(app) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url=BASE) as client:
        resp = await client.get("/api/v1/git/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    integrate_props = schema["components"]["schemas"]["IntegrateRequest"]["properties"]
    assert "green_gate_cmd" not in integrate_props


@pytest.mark.asyncio
async def test_concurrent_integrate_serializes(app) -> None:
    """Second /integrate waits on FifoCapacityGate(limit=1), not a lock."""
    order: list[str] = []

    async def slow_integrate(**_kwargs: Any) -> dict[str, Any]:
        order.append("start")
        await asyncio.sleep(0.15)
        order.append("end")
        return {
            "integration_id": "test-id",
            "status": "rejected",
            "reason_code": "mock",
            "reason": "mock",
        }

    transport = ASGITransport(app=app)
    body = {
        "arc": "x",
        "phase": "p",
        "worktree_path": "/tmp/wt",
        "approval": "ok",
        "expected_diff_sha256": "a" * 64,
        "remove_worktree": False,
    }

    with patch(
        "services.git_integration_worker.routes.integrate.integrate_op",
        side_effect=slow_integrate,
    ):
        async with AsyncClient(transport=transport, base_url=BASE) as client:
            t0 = time.monotonic()
            first = asyncio.create_task(client.post("/api/v1/git/integrate", json=body))
            await asyncio.sleep(0.05)
            second = asyncio.create_task(
                client.post("/api/v1/git/integrate", json=body)
            )
            r1, r2 = await asyncio.gather(first, second)
            elapsed = time.monotonic() - t0

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert order == ["start", "end", "start", "end"]
    assert elapsed >= 0.25


@pytest.mark.asyncio
async def test_concurrent_land_serializes(app) -> None:
    """Second /land waits on FifoCapacityGate(limit=1), same as integrate."""
    order: list[str] = []

    async def slow_land(**_kwargs: Any) -> dict[str, Any]:
        order.append("start")
        await asyncio.sleep(0.15)
        order.append("end")
        return {
            "integration_id": "test-id",
            "status": "rejected",
            "reason_code": "mock",
            "reason": "mock",
        }

    transport = ASGITransport(app=app)
    body = {
        "arc": "x",
        "phase": "p",
        "worktree_path": "/tmp/wt",
        "approval": "ok",
        "expected_diff_sha256": "a" * 64,
        "commit_message": "test",
        "remove_worktree": False,
    }

    with patch(
        "services.git_integration_worker.routes.integrate.land_op",
        side_effect=slow_land,
    ):
        async with AsyncClient(transport=transport, base_url=BASE) as client:
            t0 = time.monotonic()
            first = asyncio.create_task(client.post("/api/v1/git/land", json=body))
            await asyncio.sleep(0.05)
            second = asyncio.create_task(client.post("/api/v1/git/land", json=body))
            r1, r2 = await asyncio.gather(first, second)
            elapsed = time.monotonic() - t0

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert order == ["start", "end", "start", "end"]
    assert elapsed >= 0.25


@pytest.mark.asyncio
async def test_concurrent_cursor_sdk_dispatch_serializes(
    app, tmp_path, monkeypatch
) -> None:
    """Second cursor-sdk dispatch waits on FifoCapacityGate(limit=1)."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None

    order: list[str] = []

    async def slow_gated(**_kwargs: Any) -> None:
        order.append("start")
        await asyncio.sleep(0.15)
        order.append("end")

    transport = ASGITransport(app=app)
    body = {
        "thread_id": "t1",
        "model": "cursor/composer-2.5",
        "dispatch_id": "disp-ser-1",
        "execution_id": "exec-ser-1",
        "message": "hello",
    }
    body2 = {**body, "thread_id": "t2", "dispatch_id": "disp-ser-2", "execution_id": "exec-ser-2"}

    with (
        patch(
            "services.git_integration_worker.routes.cursor_sdk._run_sdk_dispatch_gated",
            side_effect=slow_gated,
        ),
        patch(
            "services.git_integration_worker.routes.cursor_sdk.validate_dispatch_context",
            return_value={"ok": True},
        ),
    ):
        async with AsyncClient(transport=transport, base_url=BASE) as client:
            first = asyncio.create_task(
                client.post("/api/v1/cursor/dispatch", json=body)
            )
            await asyncio.sleep(0.05)
            second = asyncio.create_task(
                client.post("/api/v1/cursor/dispatch", json=body2)
            )
            r1, r2 = await asyncio.gather(first, second)
            # Let both background dispatch tasks drain through the gate.
            await asyncio.sleep(0.35)

    assert r1.status_code == 200
    assert r2.status_code == 200
    # Non-interleaved order is the serialization proof; a wall-clock bound here
    # would be satisfied by the fixed 0.35s drain sleep and prove nothing.
    assert order == ["start", "end", "start", "end"]
    CursorDispatchLedger._instance = None
