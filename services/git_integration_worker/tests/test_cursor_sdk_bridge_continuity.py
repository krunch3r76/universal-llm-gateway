"""Bridge read-idle re-arm and shutdown defer (agent-bus:10269 / friction 23057)."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from services.git_integration_worker.cursor_sdk_bridge_read_idle import (
    BRIDGE_READ_IDLE_MARGIN_S,
    bridge_read_timeout_for_idle,
    read_deadline_from_progress,
    touch_bridge_read_deadline,
)
from services.git_integration_worker.cursor_sdk_orphan import (
    active_bridge_count,
    register_active_client,
    shutdown_active_bridges,
    unregister_active_client,
)


def test_bridge_read_timeout_tracks_idle_budget() -> None:
    timeout = bridge_read_timeout_for_idle(idle_budget_s=1920.0)
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read == pytest.approx(1920.0 + BRIDGE_READ_IDLE_MARGIN_S)


def test_touch_bridge_read_deadline_rearms_from_tool_progress() -> None:
    transport = MagicMock()
    transport.stream_timeout = httpx.Timeout(connect=30.0, read=600.0, write=120.0, pool=60.0)
    client = MagicMock(_transport=transport)
    touch_bridge_read_deadline(client, idle_budget_s=1800.0)
    updated = transport.stream_timeout
    assert isinstance(updated, httpx.Timeout)
    assert updated.read == pytest.approx(1800.0 + BRIDGE_READ_IDLE_MARGIN_S)


def test_read_deadline_from_progress_uses_last_tool_call() -> None:
    deadline = read_deadline_from_progress(
        last_progress_at=100.0,
        idle_budget_s=1800.0,
        now=500.0,
    )
    assert deadline == pytest.approx(100.0 + 1800.0 + BRIDGE_READ_IDLE_MARGIN_S)


def test_shutdown_active_bridges_deferred_when_registry_nonempty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []

    class _FakeClient:
        def close(self) -> None:
            closed.append("closed")

    register_active_client(dispatch_id="d-defer", client=_FakeClient())  # type: ignore[arg-type]
    try:
        assert active_bridge_count() == 1
        aborted = shutdown_active_bridges(defer=True)
        assert aborted == 0
        assert closed == []
        aborted = shutdown_active_bridges(defer=False)
        assert aborted == 1
        assert closed == ["closed"]
        assert active_bridge_count() == 0
    finally:
        unregister_active_client(dispatch_id="d-defer")
