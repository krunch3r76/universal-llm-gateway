"""Relay-shape handler tests for grokbuild MCP tool (V2).

Verifies that each op sends the correct HTTP method + path to the worker,
that path parameters are substituted correctly, that the body JSON carries
the right fields, and that HTTP error responses are mapped back to the MCP
error envelope shape.

Subprocess-spawn tests from V1 are deleted (sole-maintainer constraint):
the worker owns subprocess execution; relay tests only assert the HTTP
translation layer.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tools.grokbuild import grokbuild

# ---------------------------------------------------------------------------
# Mock transport helpers
# ---------------------------------------------------------------------------


class _MockTransport(httpx.AsyncBaseTransport):
    """Intercepts httpx requests and returns a pre-programmed response."""

    def __init__(self, handler: Any) -> None:
        self._handler = handler
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def _json_response(body: Any, *, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


def _error_response(status: int, reason_code: str, reason: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={"detail": {"reason_code": reason_code, "reason": reason}},
    )


@pytest.fixture()
def patch_client(monkeypatch: pytest.MonkeyPatch):
    """Return a factory that installs a mock transport on make_async_client."""

    def _install(handler: Any) -> _MockTransport:
        transport = _MockTransport(handler)

        def _fake_make_async_client(
            url: str = "", *, timeout: float = 30.0
        ) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=transport, base_url="http://testhost")

        monkeypatch.setattr(
            "tools.grokbuild.make_async_client",
            _fake_make_async_client,
        )
        return transport

    return _install


# ---------------------------------------------------------------------------
# Correct method + path per op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_models_op_sends_get(patch_client: Any) -> None:
    transport = patch_client(lambda _r: _json_response({"models": []}))
    out = await grokbuild("models")
    assert out == {"models": []}
    req = transport.requests[0]
    assert req.method == "GET"
    assert req.url.path == "/api/v1/grokbuild/models"


@pytest.mark.asyncio
async def test_worktree_list_sends_get(patch_client: Any) -> None:
    transport = patch_client(lambda _r: _json_response({"worktrees": []}))
    await grokbuild("worktree_list")
    req = transport.requests[0]
    assert req.method == "GET"
    assert req.url.path == "/api/v1/grokbuild/worktrees"


@pytest.mark.asyncio
async def test_worktree_create_sends_post_with_body(patch_client: Any) -> None:
    transport = patch_client(lambda _r: _json_response({"status": "completed"}))
    await grokbuild("worktree_create", name="wt1", branch="feat/x", source_repo="/repo")
    req = transport.requests[0]
    assert req.method == "POST"
    assert req.url.path == "/api/v1/grokbuild/worktrees"
    body = json.loads(req.content)
    assert body["name"] == "wt1"
    assert body["branch"] == "feat/x"
    assert body["source_repo"] == "/repo"
    # Extra MCP params must NOT appear in the body.
    assert "cwd" not in body
    assert "mode" not in body


@pytest.mark.asyncio
async def test_worktree_remove_substitutes_path_param(patch_client: Any) -> None:
    transport = patch_client(lambda _r: _json_response({"status": "completed"}))
    await grokbuild("worktree_remove", name="wt1")
    req = transport.requests[0]
    assert req.method == "DELETE"
    assert req.url.path == "/api/v1/grokbuild/worktrees/wt1"


@pytest.mark.asyncio
async def test_push_substitutes_name_and_sends_body(patch_client: Any) -> None:
    transport = patch_client(lambda _r: _json_response({"status": "completed"}))
    await grokbuild(
        "push", name="wt1", remote="origin", branch="feat/x", set_upstream=True
    )
    req = transport.requests[0]
    assert req.method == "POST"
    assert req.url.path == "/api/v1/grokbuild/worktrees/wt1/push"
    body = json.loads(req.content)
    assert body["remote"] == "origin"
    assert body["branch"] == "feat/x"
    assert body["set_upstream"] is True
    assert "name" not in body  # consumed as path param


@pytest.mark.asyncio
async def test_pr_create_substitutes_name_and_sends_body(patch_client: Any) -> None:
    transport = patch_client(lambda _r: _json_response({"status": "completed"}))
    await grokbuild(
        "pr_create",
        name="wt1",
        pr_title="My PR",
        pr_body="desc",
        pr_base="main",
        pr_head="feat/x",
        draft=True,
    )
    req = transport.requests[0]
    assert req.method == "POST"
    assert req.url.path == "/api/v1/grokbuild/worktrees/wt1/pull-requests"
    body = json.loads(req.content)
    assert body["pr_title"] == "My PR"
    assert body["draft"] is True
    assert "name" not in body


@pytest.mark.asyncio
async def test_fetch_result_sends_get_with_dispatch_id(patch_client: Any) -> None:
    transport = patch_client(
        lambda _r: _json_response({"status": "completed", "stdout": "ok"})
    )
    await grokbuild("fetch_result", dispatch_id="d-123", format="text")
    req = transport.requests[0]
    assert req.method == "GET"
    assert req.url.path == "/api/v1/grokbuild/dispatches/d-123/result"
    # format goes as a query param for GET
    assert req.url.params.get("format") == "text"


@pytest.mark.asyncio
async def test_build_sends_post_and_returns_202_envelope(patch_client: Any) -> None:
    accepted = {
        "dispatch_id": "d-456",
        "status_url": "/api/v1/grokbuild/dispatches/d-456",
        "events_url": "/api/v1/grokbuild/dispatches/d-456/events",
        "state": "pending",
    }
    transport = patch_client(lambda _r: httpx.Response(202, json=accepted))
    out = await grokbuild("build", cwd="/repo", prompt="do stuff", tier="quick")
    assert out == accepted
    req = transport.requests[0]
    assert req.method == "POST"
    assert req.url.path == "/api/v1/grokbuild/dispatches"
    body = json.loads(req.content)
    assert body["cwd"] == "/repo"
    assert body["prompt"] == "do stuff"
    assert body["tier"] == "quick"
    # Unrelated params must NOT appear.
    assert "name" not in body
    assert "pr_title" not in body


@pytest.mark.asyncio
async def test_build_status_sends_get_with_dispatch_id(patch_client: Any) -> None:
    status_body = {"dispatch_id": "d-456", "state": "running"}
    transport = patch_client(lambda _r: _json_response(status_body))
    out = await grokbuild("build_status", dispatch_id="d-456")
    assert out["dispatch_id"] == "d-456"
    req = transport.requests[0]
    assert req.method == "GET"
    assert req.url.path == "/api/v1/grokbuild/dispatches/d-456"


@pytest.mark.asyncio
async def test_build_cancel_sends_delete(patch_client: Any) -> None:
    cancel_body = {
        "dispatch_id": "d-456",
        "state": "cancelled",
        "signal_used": "SIGTERM",
        "reason": "operator_cancel",
    }
    transport = patch_client(lambda _r: _json_response(cancel_body))
    out = await grokbuild("build_cancel", dispatch_id="d-456")
    assert out["state"] == "cancelled"
    req = transport.requests[0]
    assert req.method == "DELETE"
    assert req.url.path == "/api/v1/grokbuild/dispatches/d-456"


# ---------------------------------------------------------------------------
# HTTP error → MCP error envelope mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_4xx_maps_to_rejected_envelope(patch_client: Any) -> None:
    patch_client(
        lambda _r: _error_response(404, "worktree_not_found", "no such worktree")
    )
    out = await grokbuild("worktree_remove", name="missing")
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "worktree_not_found"
    assert "no such worktree" in out["metadata"]["reason"]


@pytest.mark.asyncio
async def test_5xx_maps_to_failed_envelope(patch_client: Any) -> None:
    patch_client(lambda _r: _error_response(502, "op_failed", "upstream died"))
    out = await grokbuild("models")
    assert out["status"] == "failed"
    assert out["metadata"]["reason_code"] == "op_failed"


@pytest.mark.asyncio
async def test_429_capacity_exhausted_maps_to_rejected(patch_client: Any) -> None:
    body = {
        "reason_code": "capacity_exhausted",
        "reason": "all 4 slots busy",
        "running": 4,
        "capacity": 4,
    }
    patch_client(
        lambda _r: httpx.Response(429, json=body, headers={"Retry-After": "30"})
    )
    out = await grokbuild("build", cwd="/repo", prompt="do stuff")
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "capacity_exhausted"
    assert out["metadata"]["running"] == 4


# ---------------------------------------------------------------------------
# Local relay-layer rejections (no HTTP call issued)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_op_rejected_locally(patch_client: Any) -> None:
    transport = patch_client(lambda _r: _json_response({}))
    out = await grokbuild("worktree")  # type: ignore[arg-type]
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "unknown_op"
    # No HTTP request was issued.
    assert len(transport.requests) == 0


@pytest.mark.asyncio
async def test_retired_dispatch_op_rejected_locally(patch_client: Any) -> None:
    transport = patch_client(lambda _r: _json_response({}))
    out = await grokbuild("dispatch")
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "retired_op"
    assert len(transport.requests) == 0
