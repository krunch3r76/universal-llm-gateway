"""F6: integration tests for is_pending_empty_worker_thread (real agent-bus GET shape)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, init_db
from agent_bus_store.db.turns import insert_turn

from systems.frontier_consult.cursor_sdk_thread_reuse import (
    is_pending_empty_worker_thread,
)


@pytest.fixture
def _bus_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    monkeypatch.setenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "1")
    init_db()
    app = create_app(db_path=str(db_path))
    app.dependency_overrides[require_token] = lambda: None
    transport = httpx.ASGITransport(app=app)

    def _client_factory(_url: str, *, timeout: float = 10.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=transport, base_url="http://test", timeout=timeout
        )

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.make_async_client",
        _client_factory,
    )
    return app


@pytest.mark.asyncio
async def test_pending_empty_shell_returns_true(_bus_env) -> None:
    row = create_thread(thread_id=None, slug="pending-shell", lifecycle_state="pending")
    assert row is not None
    thread_id = row["id"]

    assert await is_pending_empty_worker_thread(thread_id) is True


@pytest.mark.asyncio
async def test_pending_with_turn_returns_false(_bus_env) -> None:
    row = create_thread(
        thread_id=None, slug="pending-with-turn", lifecycle_state="pending"
    )
    assert row is not None
    thread_id = row["id"]
    insert_turn(
        thread=thread_id,
        from_agent="cursor",
        to_agent="cursor-sdk",
        subject="pointer",
        body="seed",
        status="open",
    )

    assert await is_pending_empty_worker_thread(thread_id) is False


@pytest.mark.asyncio
async def test_active_empty_thread_returns_false(_bus_env) -> None:
    row = create_thread(thread_id=None, slug="active-empty", lifecycle_state="active")
    assert row is not None

    assert await is_pending_empty_worker_thread(row["id"]) is False


@pytest.mark.asyncio
async def test_missing_thread_returns_false(_bus_env) -> None:
    assert await is_pending_empty_worker_thread("99999") is False


@pytest.mark.asyncio
async def test_non_numeric_thread_id_skips_probe(
    _bus_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = AsyncMock()
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.make_async_client",
        factory,
    )

    assert await is_pending_empty_worker_thread("fastmcp-post-p0") is False
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_http_error_returns_false(
    _bus_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _BrokenClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            raise httpx.ConnectError("bus down")

    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_thread_reuse.make_async_client",
        lambda *_a, **_k: _BrokenClient(),
    )

    assert await is_pending_empty_worker_thread("42") is False


@pytest.mark.asyncio
async def test_no_bus_token_configured_returns_false(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    monkeypatch.delenv("AGENT_BUS_TOKEN", raising=False)
    monkeypatch.delenv("ALLOW_UNSET_AGENT_BUS_TOKEN", raising=False)
    init_db()
    row = create_thread(thread_id=None, slug="no-token", lifecycle_state="pending")
    assert row is not None

    assert await is_pending_empty_worker_thread(row["id"]) is False
