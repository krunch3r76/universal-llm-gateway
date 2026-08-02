"""Drain middleware — /active-work snapshot + life activity TTL."""

from __future__ import annotations

import time

import pytest
from middleware.drain import (
    DrainMiddleware,
    active_work_snapshot,
    begin_drain,
    note_life_tools_activity,
    reset_drain_for_tests,
)
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _reset_drain() -> None:
    reset_drain_for_tests()


def _app() -> DrainMiddleware:
    async def ok(_request):  # noqa: ANN001
        return JSONResponse({"ok": True})

    return DrainMiddleware(Starlette(routes=[Route("/elsewhere", ok)]))


def test_active_work_idle_by_default() -> None:
    snap = active_work_snapshot()
    assert snap["busy"] is False
    assert snap["in_flight"] == 0
    assert snap["life_hot"] is False


def test_life_tools_activity_marks_busy_within_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_LIFE_ACTIVITY_TTL_S", "60")
    # Re-import TTL is module-load; force via note + monkeypatch of module const.
    import middleware.drain as drain_mod

    monkeypatch.setattr(drain_mod, "_LIFE_ACTIVITY_TTL_S", 60.0)
    note_life_tools_activity()
    snap = active_work_snapshot()
    assert snap["busy"] is True
    assert snap["life_hot"] is True
    assert snap["life_idle_s"] is not None
    assert snap["life_idle_s"] < 1.0


def test_life_tools_activity_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    import middleware.drain as drain_mod

    monkeypatch.setattr(drain_mod, "_LIFE_ACTIVITY_TTL_S", 0.05)
    note_life_tools_activity()
    time.sleep(0.06)
    snap = active_work_snapshot()
    assert snap["life_hot"] is False
    assert snap["busy"] is False


def test_active_work_http_endpoint() -> None:
    with TestClient(_app()) as client:
        resp = client.get("/active-work")
    assert resp.status_code == 200
    body = resp.json()
    assert body["busy"] is False
    assert "in_flight" in body
    assert "life_activity_ttl_s" in body


def test_active_work_during_drain_reports_draining() -> None:
    begin_drain(reason="test", timeout_s=1.0)
    with TestClient(_app()) as client:
        resp = client.get("/active-work")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "draining"
    assert body["draining"] is True
