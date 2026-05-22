"""MQ3 recursion depth enforcement tests (G8).

Covers the depth=3 rejection boundary at the worker route layer.
No grok subprocess is spawned — the tracker rejects at the admission
stage before ``dispatch_op`` is ever called.

Preconditions:
* G6: ``GROKBUILD_RECURSION_DEPTH`` in ``runner_argv._ALLOW`` (landed in Phase 1).
* G7: ``GrokbuildDispatchRequest.recursion_depth`` field present;
  ``POST /dispatches`` enforces depth ≤ 2.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

BASE = "http://test"

_BASE_BODY: dict[str, Any] = {
    "cwd": "/tmp/some-wt",
    "prompt": "do a thing",
    "mode": "read_only",
}


@pytest.fixture(autouse=True)
def _no_uds(monkeypatch):
    monkeypatch.setattr(
        "services.grokbuild_worker.events._emit_uds", lambda _event: None
    )


@pytest.fixture
def app():
    """Fresh app per test with an initialized tracker on app.state."""
    from services.grokbuild_worker.app import create_app
    from services.grokbuild_worker.tracker import GrokbuildExecutionTracker

    app = create_app()
    app.state.grokbuild_tracker = GrokbuildExecutionTracker(ttl_seconds=3600)
    return app


@pytest.mark.asyncio
async def test_recursion_depth_3_rejected(app):
    """depth=3 exceeds the limit of 2 → 422 recursion_depth_exceeded."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as client:
        resp = await client.post(
            "/api/v1/grokbuild/dispatches",
            json={**_BASE_BODY, "recursion_depth": 3},
        )
    assert resp.status_code == 422
    body = resp.json()
    assert body["reason_code"] == "recursion_depth_exceeded"
    assert body["depth_received"] == 3
    assert body["depth_limit"] == 2


@pytest.mark.asyncio
async def test_recursion_depth_2_admitted(app):
    """depth=2 is at the limit → 202 Accepted (no subprocess launched in test)."""
    from unittest.mock import AsyncMock, patch

    _completed: dict[str, Any] = {
        "dispatch_id": "placeholder",
        "status": "completed",
        "stdout": "ok",
        "stderr": "",
        "exit_code": 0,
        "duration_s": 0.05,
        "sidecar_path": None,
        "metadata": {"reason_code": "", "reason": ""},
    }
    with patch(
        "services.grokbuild_worker.tracker_runner.dispatch_op",
        new_callable=AsyncMock,
        return_value=_completed,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=BASE
        ) as client:
            resp = await client.post(
                "/api/v1/grokbuild/dispatches",
                json={**_BASE_BODY, "recursion_depth": 2},
            )
    assert resp.status_code == 202
    body = resp.json()
    assert "dispatch_id" in body
    assert body["state"] == "pending"


@pytest.mark.asyncio
async def test_recursion_depth_none_admitted(app):
    """depth=None (omitted) → 202 Accepted; no depth enforcement applied."""
    from unittest.mock import AsyncMock, patch

    _completed: dict[str, Any] = {
        "dispatch_id": "placeholder",
        "status": "completed",
        "stdout": "ok",
        "stderr": "",
        "exit_code": 0,
        "duration_s": 0.05,
        "sidecar_path": None,
        "metadata": {"reason_code": "", "reason": ""},
    }
    with patch(
        "services.grokbuild_worker.tracker_runner.dispatch_op",
        new_callable=AsyncMock,
        return_value=_completed,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=BASE
        ) as client:
            resp = await client.post(
                "/api/v1/grokbuild/dispatches",
                json=_BASE_BODY,
            )
    assert resp.status_code == 202


@pytest.mark.asyncio
async def test_recursion_depth_4_rejected_carries_received_value(app):
    """depth=4 rejection envelope carries the exact received value."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as client:
        resp = await client.post(
            "/api/v1/grokbuild/dispatches",
            json={**_BASE_BODY, "recursion_depth": 4},
        )
    assert resp.status_code == 422
    body = resp.json()
    assert body["depth_received"] == 4
    assert body["reason_code"] == "recursion_depth_exceeded"
