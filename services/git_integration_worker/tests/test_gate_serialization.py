"""Concurrency and OpenAPI tests for git-integration-worker."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

BASE = "http://test"

# Merge-out paths refuse dirty checked-out master before integrate_op/land_op.
# Keep the guard live but force a clean reading so the gate/op path is exercised.
_CLEAN_MASTER = (
    "services.git_integration_worker.cursor_sdk_land_lease.checked_out_master_dirty"
)


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

    with (
        patch(
            "services.git_integration_worker.routes.integrate.integrate_op",
            side_effect=slow_integrate,
        ),
        patch(_CLEAN_MASTER, return_value=(False, "")),
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

    with (
        patch(
            "services.git_integration_worker.routes.integrate.land_op",
            side_effect=slow_land,
        ),
        patch(_CLEAN_MASTER, return_value=(False, "")),
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
    """Second writer is 202-queued; capacity + promote keep runs non-interleaved."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )
    from services.git_integration_worker.cursor_sdk_closeout import SdkRunOutcome
    from services.git_integration_worker.cursor_sdk_lane_regime import set_lane_b_regime
    from services.git_integration_worker.cursor_sdk_park import (
        release_or_restore_for_child_sync,
    )
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    set_lane_b_regime(active=False)

    order: list[str] = []

    def slow_sync(**kwargs: Any) -> SdkRunOutcome:
        # Stub the worker body only — keep acquire/release/promote on the real path.
        order.append("start")
        time.sleep(0.15)
        order.append("end")
        release_or_restore_for_child_sync(
            kwargs["gate_loop"], dispatch_id=kwargs["ctx"].dispatch_id
        )
        return SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=100,
            tool_call_count=1,
            sdk_request_id="sdk-req-ser",
            request_id_source="stream",
        )

    async def finalize_and_promote(*, req: Any, controller: Any, **_kwargs: Any) -> None:
        await route_mod._mark_terminal_and_promote(
            dispatch_id=req.dispatch_id,
            terminal_status="completed",
            controller=controller,
            emit_tag="CURSOR_TEST_SERIALIZE",
        )

    async def fail_and_promote(*, req: Any, controller: Any, **_kwargs: Any) -> None:
        await route_mod._mark_terminal_and_promote(
            dispatch_id=req.dispatch_id,
            terminal_status="failed",
            controller=controller,
            emit_tag="CURSOR_TEST_SERIALIZE",
        )

    transport = ASGITransport(app=app)
    body = {
        "thread_id": "t1",
        "model": "cursor/composer-2.5",
        "dispatch_id": "disp-ser-1",
        "execution_id": "exec-ser-1",
        "handoff_contract": "implement",
        "message": "TYPE: DIRECTIVE\ncontract: implement\n",
    }
    body2 = {
        **body,
        "thread_id": "t2",
        "dispatch_id": "disp-ser-2",
        "execution_id": "exec-ser-2",
    }

    with (
        patch.object(route_mod, "_run_sdk_sync", side_effect=slow_sync),
        patch.object(
            route_mod, "validate_dispatch_context", return_value={"ok": True}
        ),
        patch.object(route_mod, "capture_wt_baseline_with_hashes", lambda *_a, **_k: {}),
        patch.object(route_mod, "_finalize_success", side_effect=finalize_and_promote),
        patch.object(route_mod, "_finalize_failed", side_effect=fail_and_promote),
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
            # Wait for first terminal → promote → second gated run (not wall-clock alone).
            for _ in range(40):
                if order == ["start", "end", "start", "end"]:
                    break
                await asyncio.sleep(0.05)

    assert r1.status_code == 200
    # Queued behind the write-lease holder is the live admission contract.
    assert r2.status_code == 202
    assert r2.json().get("status") == "queued"
    assert order == ["start", "end", "start", "end"]
    CursorDispatchLedger._instance = None
