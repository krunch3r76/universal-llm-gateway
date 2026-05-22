"""Worker-level tests for Phase B async build endpoints.

Covers:
* POST → 202 + Location header round-trips to a valid status URL.
* Status transitions pending → running → succeeded (mocked dispatch_op).
* Cancel during running → 200; transitions to cancelled.
* Cancel on terminal → 409.
* TTL expiry: completed dispatch returns 404 after retention window.
* Orphan cleanup at boot: pre-seeded dead-pid entry is purged.
* SSE: events stream until terminal then close.
* Concurrent-build cap honored: exceed cap → 429 + Retry-After.

``dispatch_op`` is mocked via ``unittest.mock.AsyncMock`` so no grok
subprocess is spawned. The tracker still drives the full state machine
because it owns the entry transitions, not the lib.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

BASE = "http://test"

_COMPLETED_ENV: dict[str, Any] = {
    "dispatch_id": "placeholder",
    "status": "completed",
    "stdout": "ok",
    "stderr": "",
    "exit_code": 0,
    "duration_s": 0.05,
    "sidecar_path": None,
    "metadata": {"reason_code": "", "reason": ""},
}

_FAILED_ENV: dict[str, Any] = {
    **_COMPLETED_ENV,
    "status": "failed",
    "exit_code": 2,
    "metadata": {"reason_code": "grok_nonzero_exit", "reason": "exited 2"},
}


_REQUEST_BODY: dict[str, Any] = {
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


def _patch_dispatch_op(envelope: dict[str, Any] | None = None, *, delay: float = 0.0):
    """Return an AsyncMock that pretends to be dispatch_op."""
    env = dict(envelope or _COMPLETED_ENV)

    async def _impl(**kwargs: Any) -> dict[str, Any]:
        env["dispatch_id"] = kwargs.get("dispatch_id", env["dispatch_id"])
        if delay:
            await asyncio.sleep(delay)
        return env

    return AsyncMock(side_effect=_impl)


@pytest.mark.asyncio
async def test_post_accepts_and_returns_location(app):
    with patch(
        "services.grokbuild_worker.tracker_runner.dispatch_op",
        new=_patch_dispatch_op(),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as c:
            resp = await c.post("/api/v1/grokbuild/dispatches", json=_REQUEST_BODY)
    assert resp.status_code == 202
    body = resp.json()
    assert body["dispatch_id"]
    assert body["status_url"].endswith(body["dispatch_id"])
    assert resp.headers["location"].endswith(body["dispatch_id"])


@pytest.mark.asyncio
async def test_status_succeeded_after_run(app):
    with patch(
        "services.grokbuild_worker.tracker_runner.dispatch_op",
        new=_patch_dispatch_op(),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as c:
            resp = await c.post("/api/v1/grokbuild/dispatches", json=_REQUEST_BODY)
            dispatch_id = resp.json()["dispatch_id"]
            # Drive the tracker's background task to completion.
            tracker = app.state.grokbuild_tracker
            await tracker._dispatches[dispatch_id].task  # noqa: SLF001
            status = await c.get(f"/api/v1/grokbuild/dispatches/{dispatch_id}")
    assert status.status_code == 200
    payload = status.json()
    assert payload["state"] == "succeeded"
    assert payload["result_available"] is True


@pytest.mark.asyncio
async def test_status_404_when_unknown(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as c:
        resp = await c.get("/api/v1/grokbuild/dispatches/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["detail"]["reason_code"] == "dispatch_not_found"


@pytest.mark.asyncio
async def test_cancel_running_returns_200(app):
    async def _slow(**_: Any) -> dict[str, Any]:
        await asyncio.sleep(5.0)
        return _COMPLETED_ENV

    with patch(
        "services.grokbuild_worker.tracker_runner.dispatch_op",
        new=AsyncMock(side_effect=_slow),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as c:
            resp = await c.post("/api/v1/grokbuild/dispatches", json=_REQUEST_BODY)
            dispatch_id = resp.json()["dispatch_id"]
            # Give the task a chance to enter "running" state.
            await asyncio.sleep(0.05)
            cancel = await c.delete(f"/api/v1/grokbuild/dispatches/{dispatch_id}")
    assert cancel.status_code == 200
    body = cancel.json()
    assert body["state"] == "cancelled"
    assert body["signal_used"] in {"SIGTERM", "SIGKILL", "task_cancel"}


@pytest.mark.asyncio
async def test_cancel_on_terminal_returns_409(app):
    with patch(
        "services.grokbuild_worker.tracker_runner.dispatch_op",
        new=_patch_dispatch_op(),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as c:
            resp = await c.post("/api/v1/grokbuild/dispatches", json=_REQUEST_BODY)
            dispatch_id = resp.json()["dispatch_id"]
            tracker = app.state.grokbuild_tracker
            await tracker._dispatches[dispatch_id].task  # noqa: SLF001
            cancel = await c.delete(f"/api/v1/grokbuild/dispatches/{dispatch_id}")
    assert cancel.status_code == 409


@pytest.mark.asyncio
async def test_ttl_expiry_returns_404():
    """A completed dispatch beyond TTL is purged on subsequent status lookup."""
    from services.grokbuild_worker.app import create_app
    from services.grokbuild_worker.tracker import GrokbuildExecutionTracker

    app = create_app()
    tracker = GrokbuildExecutionTracker(ttl_seconds=0.0)
    app.state.grokbuild_tracker = tracker
    with patch(
        "services.grokbuild_worker.tracker_runner.dispatch_op",
        new=_patch_dispatch_op(),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as c:
            resp = await c.post("/api/v1/grokbuild/dispatches", json=_REQUEST_BODY)
            dispatch_id = resp.json()["dispatch_id"]
            await tracker._dispatches[dispatch_id].task  # noqa: SLF001
            # TTL=0 → next status call sweeps the terminal entry.
            await asyncio.sleep(0.01)
            status = await c.get(f"/api/v1/grokbuild/dispatches/{dispatch_id}")
    assert status.status_code == 404


@pytest.mark.asyncio
async def test_orphan_cleanup_purges_dead_pid():
    """``cleanup_orphans`` purges entries whose pid is no longer alive."""
    from services.grokbuild_worker.models.async_dispatch import (
        GrokbuildDispatchRequest,
    )
    from services.grokbuild_worker.tracker import (
        GrokbuildExecutionTracker,
        _Entry,
    )

    tracker = GrokbuildExecutionTracker()
    req = GrokbuildDispatchRequest(cwd="/tmp/x", prompt="p", mode="read_only")
    # PID 2**31-1 is reserved/unused on Linux → not alive.
    entry = _Entry(
        dispatch_id="orphan-1",
        state="running",
        request=req,
        pid_holder=[2**31 - 1],
    )
    tracker._seed_for_test(entry)  # noqa: SLF001
    purged = await tracker.cleanup_orphans()
    assert purged == 1
    assert await tracker.status("orphan-1") is None


@pytest.mark.asyncio
async def test_sse_streams_until_terminal(app):
    with patch(
        "services.grokbuild_worker.tracker_runner.dispatch_op",
        new=_patch_dispatch_op(),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as c:
            resp = await c.post("/api/v1/grokbuild/dispatches", json=_REQUEST_BODY)
            dispatch_id = resp.json()["dispatch_id"]
            chunks: list[str] = []
            async with c.stream(
                "GET", f"/api/v1/grokbuild/dispatches/{dispatch_id}/events"
            ) as stream:
                async for line in stream.aiter_lines():
                    chunks.append(line)
    body = "\n".join(chunks)
    assert "event: snapshot" in body or "event: completed" in body
    assert "event: completed" in body


@pytest.mark.asyncio
async def test_capacity_cap_returns_429():
    """Operator answer 1c: 4 in-flight cap → 429 + Retry-After."""
    from services.grokbuild_worker.app import create_app
    from services.grokbuild_worker.tracker import GrokbuildExecutionTracker

    app = create_app()
    tracker = GrokbuildExecutionTracker(capacity=2, ttl_seconds=3600)
    app.state.grokbuild_tracker = tracker

    async def _slow(**_: Any) -> dict[str, Any]:
        await asyncio.sleep(2.0)
        return _COMPLETED_ENV

    with patch(
        "services.grokbuild_worker.tracker_runner.dispatch_op",
        new=AsyncMock(side_effect=_slow),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as c:
            await c.post("/api/v1/grokbuild/dispatches", json=_REQUEST_BODY)
            await c.post("/api/v1/grokbuild/dispatches", json=_REQUEST_BODY)
            resp = await c.post("/api/v1/grokbuild/dispatches", json=_REQUEST_BODY)
    assert resp.status_code == 429
    assert resp.headers.get("retry-after") == "30"
    assert resp.json()["reason_code"] == "capacity_exhausted"
