"""Tape budget fail-closed and degrade-basis tests (agent-bus:99998)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agent_bus_store.auth import require_token
from agent_bus_store.server import create_app
from agent_bus_store.tape_degrade import (
    TapeBudgetExceeded,
    degrade_overflow_messages,
    payload_bytes,
)
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline

_UUID = "c3d4e5f6-a7b8-9012-cdef-123456789012"


def _large_messages(count: int = 5, content_len: int = 800) -> list[dict]:
    return [
        {
            "role": "user",
            "content": "x" * content_len,
            "session_id": "s1",
            "transcript_id": _UUID,
            "turn_index": i,
        }
        for i in range(1, count + 1)
    ]


def test_degrade_raises_when_single_body_exceeds_budget() -> None:
    messages = _large_messages(count=1, content_len=5000)
    with pytest.raises(TapeBudgetExceeded) as exc_info:
        degrade_overflow_messages(
            messages,
            cells=[],
            budget_bytes=2048,
            thread_id="10469",
        )
    exc = exc_info.value
    assert exc.thread_id == "10469"
    assert exc.budget_bytes == 2048
    assert exc.required_budget_bytes > 2048
    assert exc.min_viable_budget_bytes > 2048


def test_degrade_drops_index_rows_when_body_fits() -> None:
    """Budget fits one body but not accumulated index rows → index_dropped > 0."""
    messages = _large_messages(count=5, content_len=500)
    budget = 1200
    kept, index_rows, truncated, degraded = degrade_overflow_messages(
        messages,
        cells=[],
        budget_bytes=budget,
        thread_id="10469",
    )
    assert truncated is True
    assert payload_bytes(kept, index_rows) <= budget
    assert degraded is not None
    assert degraded["index_dropped"] > 0
    assert degraded["overflow_uri"].startswith("/threads/10469/tape?")


def test_tape_route_budget_exceeded_413(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None

    def _raise_budget(**_kwargs: object) -> dict:
        raise TapeBudgetExceeded(
            thread_id="10469",
            budget_bytes=2048,
            required_budget_bytes=50000,
            min_viable_budget_bytes=3000,
        )

    with (
        TestClient(app) as client,
        patch(
            "agent_bus_store.routes.threads.tape.classify_thread",
            return_value={"spine": "root"},
        ),
        patch("agent_bus_store.routes.threads.tape.load_thread_tags", return_value=[]),
        patch(
            "agent_bus_store.routes.threads.tape.render_tape_with_harvest",
            side_effect=_raise_budget,
        ),
    ):
        resp = client.get("/threads/10469/tape?budget_bytes=2048")

    assert resp.status_code == 413
    detail = resp.json()["detail"]
    assert detail["error"] == "tape_budget_exceeded"
    assert detail["thread_id"] == "10469"
    assert "messages" not in detail
    assert "index" not in detail


def test_tape_route_degraded_200_honours_budget(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    messages = _large_messages(count=3, content_len=600)
    kept, index_rows, truncated, degraded = degrade_overflow_messages(
        messages,
        cells=[],
        budget_bytes=2048,
        thread_id="10469",
    )
    tape_payload = {
        "open_line": {
            "thread_id": "10469",
            "truncated": truncated,
            "budget_bytes": 2048,
            "payload_bytes": payload_bytes(kept, index_rows),
            "degraded": degraded,
        },
        "messages": kept,
        "index": index_rows,
        "truncated": truncated,
        "degraded": degraded,
        "segments": [],
        "cells": [],
        "meta": {},
    }

    with (
        TestClient(app) as client,
        patch(
            "agent_bus_store.routes.threads.tape.classify_thread",
            return_value={"spine": "root"},
        ),
        patch("agent_bus_store.routes.threads.tape.load_thread_tags", return_value=[]),
        patch(
            "agent_bus_store.routes.threads.tape.render_tape_with_harvest",
            return_value=tape_payload,
        ),
    ):
        resp = client.get("/threads/10469/tape?budget_bytes=2048")

    assert resp.status_code == 200
    body = resp.json()
    assert body["open_line"]["payload_bytes"] <= 2048
    assert body["open_line"]["degraded"]["kind"] == "budget_overflow"
    assert body["degraded"] == body["open_line"]["degraded"]


@pytest.mark.asyncio
async def test_stargate_fetch_tape_surfaces_413_budget_exceeded() -> None:
    from systems.continuity.tape_read import fetch_tape_envelope

    detail = {
        "error": "tape_budget_exceeded",
        "thread_id": "10469",
        "budget_bytes": 2048,
        "required_budget_bytes": 50000,
        "min_viable_budget_bytes": 3000,
        "overflow_uri": "/threads/10469/tape?budget_bytes=50000",
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 413
    mock_resp.json.return_value = {"detail": detail}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "systems.continuity.tape_read.make_async_client",
        return_value=mock_client,
    ):
        payload, status = await fetch_tape_envelope(
            "10469",
            budget_bytes=2048,
        )

    assert status == 413
    assert payload["error"] == "tape_budget_exceeded"
    assert "messages" not in payload
    assert payload["required_budget_bytes"] == 50000


@pytest.mark.asyncio
async def test_stargate_fetch_tape_passes_degraded_block_through() -> None:
    from systems.continuity.tape_read import fetch_tape_envelope

    degraded = {
        "kind": "budget_overflow",
        "bodies_kept": 1,
        "bodies_dropped": 2,
        "index_rows": 0,
        "index_dropped": 0,
        "budget_bytes": 2048,
        "payload_bytes": 1800,
        "required_budget_bytes": 9000,
        "overflow_uri": "/threads/10469/tape?budget_bytes=9000",
    }
    tape = {
        "messages": [{"role": "user", "content": "hi", "turn_index": 1}],
        "index": [],
        "cells": [],
        "segments": [],
        "truncated": True,
        "degraded": degraded,
        "open_line": {
            "turn_count": 1,
            "truncated": True,
            "budget_bytes": 2048,
            "payload_bytes": 1800,
            "degraded": degraded,
        },
        "meta": {"messages_sha256": "abc"},
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = tape

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "systems.continuity.tape_read.make_async_client",
        return_value=mock_client,
    ):
        payload, status = await fetch_tape_envelope("10469", budget_bytes=2048)

    assert status == 200
    assert payload["open_line"]["degraded"] == degraded
    assert payload["meta"]["degraded"] == degraded


def test_mcp_tape_relay_surfaces_413_budget_exceeded() -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services" / "mcp-server"))
    from tools.agent_bus.threads import _tape_dispatch

    relay_result = {
        "error": "HTTP 413",
        "status_code": 413,
        "detail": {
            "error": "tape_budget_exceeded",
            "thread_id": "10469",
            "budget_bytes": 2048,
            "required_budget_bytes": 50000,
            "min_viable_budget_bytes": 3000,
            "overflow_uri": "/threads/10469/tape?budget_bytes=50000",
        },
    }
    with patch("tools.agent_bus.threads.relay", return_value=relay_result):
        result = _tape_dispatch(thread="10469", budget_bytes=2048)

    assert result["status_code"] == 413
    assert result["detail"]["error"] == "tape_budget_exceeded"
    assert "messages" not in result


def test_mcp_tape_relay_passes_degraded_block_through() -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services" / "mcp-server"))
    from tools.agent_bus.threads import _tape_dispatch

    degraded = {
        "kind": "budget_overflow",
        "bodies_kept": 1,
        "bodies_dropped": 2,
        "index_rows": 1,
        "index_dropped": 1,
        "budget_bytes": 8000,
        "payload_bytes": 7500,
        "required_budget_bytes": 12000,
        "overflow_uri": "/threads/10469/tape?budget_bytes=12000",
    }
    relay_result = {
        "open_line": {"degraded": degraded, "truncated": True},
        "messages": [{"role": "user", "content": "x"}],
        "index": [],
        "truncated": True,
        "degraded": degraded,
    }
    with patch("tools.agent_bus.threads.relay", return_value=relay_result):
        result = _tape_dispatch(thread="10469", budget_bytes=8000)

    assert result["degraded"] == degraded
    assert result["open_line"]["degraded"] == degraded
