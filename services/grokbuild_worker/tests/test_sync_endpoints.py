"""Worker-level tests for the seven synchronous REST endpoints.

Coverage strategy: happy-path + one error-path per endpoint.
``libs/grokbuild`` calls are mocked (lib correctness is tested in
``libs/grokbuild/tests/``). ``_emit_uds`` is patched out so tests
never touch the event-service socket.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

BASE = "http://test"

# Minimal completed envelope returned by mocked lib ops.
_COMPLETED: dict[str, Any] = {
    "dispatch_id": "test-id",
    "status": "completed",
    "stdout": "",
    "stderr": "",
    "exit_code": 0,
    "duration_s": 0.1,
    "sidecar_path": None,
    "metadata": {
        "reason_code": "",
        "reason": "",
        "count": 1,
        "worktrees": [],
        "branch": "main",
    },
}

_REJECTED_INVALID: dict[str, Any] = {
    "dispatch_id": "test-id",
    "status": "rejected",
    "stdout": "",
    "stderr": "",
    "exit_code": None,
    "duration_s": 0.0,
    "sidecar_path": None,
    "metadata": {"reason_code": "name_invalid", "reason": "name must be non-empty"},
}

_REJECTED_NOT_FOUND: dict[str, Any] = {
    **_REJECTED_INVALID,
    "metadata": {"reason_code": "worktree_not_found", "reason": "no such worktree"},
}

_REJECTED_DISPATCH_404: dict[str, Any] = {
    **_REJECTED_INVALID,
    "metadata": {
        "reason_code": "result_not_found",
        "reason": "sidecar not found",
        "http_status": 404,
    },
}


@pytest.fixture
def app():
    """Fresh FastAPI app per test (no lifespan; routes only)."""
    from services.grokbuild_worker.app import create_app

    return create_app()


@pytest.fixture(autouse=True)
def _no_uds(monkeypatch):
    """Suppress all UDS event publishes in tests."""
    monkeypatch.setattr(
        "services.grokbuild_worker.events._emit_uds",
        lambda _event: None,
    )


# ─────────────────────────── GET /models ───────────────────────────────────


@pytest.mark.asyncio
async def test_list_models_happy(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as client:
        resp = await client.get("/api/v1/grokbuild/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert len(data["models"]) > 0
    assert "id" in data["models"][0]


@pytest.mark.asyncio
async def test_list_models_structure(app):
    """Each model entry exposes the capability flags from _ModelCapabilities."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as client:
        resp = await client.get("/api/v1/grokbuild/models")
    assert resp.status_code == 200
    first = resp.json()["models"][0]
    assert "supports_reasoning_effort" in first
    assert "supports_effort" in first


# ──────────────────────── POST /worktrees ──────────────────────────────────


@pytest.mark.asyncio
async def test_create_worktree_happy(app):
    with patch(
        "services.grokbuild_worker.routes.worktrees.worktree_create_op",
        new=AsyncMock(return_value=_COMPLETED),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=BASE
        ) as client:
            resp = await client.post(
                "/api/v1/grokbuild/worktrees",
                json={
                    "name": "my-wt",
                    "branch": "main",
                    "source_repo": "/mnt/torus/projects/foo",
                },
            )
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_create_worktree_rejected(app):
    with patch(
        "services.grokbuild_worker.routes.worktrees.worktree_create_op",
        new=AsyncMock(return_value=_REJECTED_INVALID),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=BASE
        ) as client:
            resp = await client.post(
                "/api/v1/grokbuild/worktrees",
                json={"name": "", "branch": "main", "source_repo": "/x"},
            )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["reason_code"] == "name_invalid"


# ───────────────────────── GET /worktrees ──────────────────────────────────


@pytest.mark.asyncio
async def test_list_worktrees_happy(app):
    with patch(
        "services.grokbuild_worker.routes.worktrees.worktree_list_op",
        new=AsyncMock(return_value=_COMPLETED),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=BASE
        ) as client:
            resp = await client.get("/api/v1/grokbuild/worktrees")
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_list_worktrees_failed(app):
    failed_env = {
        **_COMPLETED,
        "status": "failed",
        "metadata": {
            "reason_code": "worktree_root_unreachable",
            "reason": "fs error",
            "count": 0,
        },
    }
    with patch(
        "services.grokbuild_worker.routes.worktrees.worktree_list_op",
        new=AsyncMock(return_value=failed_env),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=BASE
        ) as client:
            resp = await client.get("/api/v1/grokbuild/worktrees")
    assert resp.status_code == 502


# ──────────────────────── DELETE /worktrees/{name} ─────────────────────────


@pytest.mark.asyncio
async def test_remove_worktree_happy(app):
    with patch(
        "services.grokbuild_worker.routes.worktrees.worktree_remove_op",
        new=AsyncMock(return_value=_COMPLETED),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=BASE
        ) as client:
            resp = await client.delete("/api/v1/grokbuild/worktrees/my-wt")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_remove_worktree_not_found(app):
    with patch(
        "services.grokbuild_worker.routes.worktrees.worktree_remove_op",
        new=AsyncMock(return_value=_REJECTED_NOT_FOUND),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=BASE
        ) as client:
            resp = await client.delete("/api/v1/grokbuild/worktrees/ghost")
    assert resp.status_code == 404


# ──────────────────── POST /worktrees/{name}/push ──────────────────────────


@pytest.mark.asyncio
async def test_push_worktree_happy(app):
    push_env = {
        **_COMPLETED,
        "metadata": {**_COMPLETED["metadata"], "branch": "feature-x"},
    }
    with patch(
        "services.grokbuild_worker.routes.worktrees.push_op",
        new=AsyncMock(return_value=push_env),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=BASE
        ) as client:
            resp = await client.post(
                "/api/v1/grokbuild/worktrees/my-wt/push",
                json={},
            )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_push_worktree_rejected(app):
    rej = {
        **_REJECTED_INVALID,
        "metadata": {"reason_code": "cwd_missing", "reason": "no cwd"},
    }
    with patch(
        "services.grokbuild_worker.routes.worktrees.push_op",
        new=AsyncMock(return_value=rej),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=BASE
        ) as client:
            resp = await client.post(
                "/api/v1/grokbuild/worktrees/ghost/push",
                json={},
            )
    assert resp.status_code == 400


# ─────────────────── POST /worktrees/{name}/pull-requests ──────────────────


@pytest.mark.asyncio
async def test_create_pr_happy(app):
    pr_env = {
        **_COMPLETED,
        "stdout": "https://github.com/org/repo/pull/99\n",
        "metadata": {
            **_COMPLETED["metadata"],
            "pr_url": "https://github.com/org/repo/pull/99",
        },
    }
    with patch(
        "services.grokbuild_worker.routes.worktrees.pr_create_op",
        new=AsyncMock(return_value=pr_env),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=BASE
        ) as client:
            resp = await client.post(
                "/api/v1/grokbuild/worktrees/my-wt/pull-requests",
                json={"pr_title": "Test PR"},
            )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_create_pr_gh_missing(app):
    rej = {
        **_REJECTED_INVALID,
        "metadata": {"reason_code": "gh_not_in_path", "reason": "gh not found"},
    }
    with patch(
        "services.grokbuild_worker.routes.worktrees.pr_create_op",
        new=AsyncMock(return_value=rej),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=BASE
        ) as client:
            resp = await client.post(
                "/api/v1/grokbuild/worktrees/my-wt/pull-requests",
                json={"pr_title": "Test PR"},
            )
    assert resp.status_code == 400
    assert resp.json()["detail"]["reason_code"] == "gh_not_in_path"


# ─────────────── GET /dispatches/{dispatch_id}/result ──────────────────────


@pytest.mark.asyncio
async def test_fetch_result_happy(app):
    result_env = {
        **_COMPLETED,
        "stdout": "agent output here",
        "metadata": {**_COMPLETED["metadata"], "format": "json"},
    }
    with patch(
        "services.grokbuild_worker.routes.dispatches.fetch_result_op",
        new=AsyncMock(return_value=result_env),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=BASE
        ) as client:
            resp = await client.get("/api/v1/grokbuild/dispatches/abc-123/result")
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_fetch_result_not_found(app):
    with patch(
        "services.grokbuild_worker.routes.dispatches.fetch_result_op",
        new=AsyncMock(return_value=_REJECTED_DISPATCH_404),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=BASE
        ) as client:
            resp = await client.get("/api/v1/grokbuild/dispatches/missing-id/result")
    assert resp.status_code == 404
    assert resp.json()["detail"]["reason_code"] == "result_not_found"


# ──────────────── extra=forbid validation ──────────────────────────────────


@pytest.mark.asyncio
async def test_extra_field_rejected(app):
    """Pydantic extra=forbid returns 422 for unknown request fields."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as client:
        resp = await client.post(
            "/api/v1/grokbuild/worktrees",
            json={
                "name": "x",
                "branch": "b",
                "source_repo": "/s",
                "unknown_field": True,
            },
        )
    assert resp.status_code == 422
