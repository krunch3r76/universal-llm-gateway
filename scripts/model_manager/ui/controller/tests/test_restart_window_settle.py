"""Tests for lifecycle wrapper success-path propagation settle (arc 6655 D)."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from scripts.model_manager.ui.controller.restart_intent_store import RestartIntentStore
from scripts.model_manager.ui.controller.restart_window_ctl import (
    lifecycle_with_restart_window,
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture
def store(tmp_path: Any) -> RestartIntentStore:
    return RestartIntentStore(db_path=tmp_path / "restart-intents.db")


def test_lifecycle_success_settles_with_boundary(store: RestartIntentStore) -> None:
    settle = AsyncMock()
    during_lifecycle: list[float] = []

    async def _lifecycle() -> str:
        during_lifecycle.append(time.monotonic())
        return "ok"

    with patch(
        "scripts.model_manager.ui.controller.restart_window_ctl.invoke_propagation_settle_for_service",
        settle,
    ):
        result = _run(
            lifecycle_with_restart_window(
                store,
                "mcp",
                "restart",
                _lifecycle,
            )
        )
    assert result == "ok"
    settle.assert_awaited_once()
    kwargs = settle.await_args.kwargs
    assert kwargs["source"] == "lifecycle_wrapper"
    boundary = kwargs["settle_not_before_monotonic"]
    assert isinstance(boundary, float)
    assert boundary <= during_lifecycle[0]
    assert kwargs["window_deadline_at"] is not None


def test_lifecycle_window_open_boundary_precedes_completion(store: RestartIntentStore) -> None:
    """Window-open monotonic must be captured before lifecycle completes (AC-P6)."""
    captured: list[float] = []
    during_lifecycle: list[float] = []

    async def _lifecycle() -> str:
        during_lifecycle.append(time.monotonic())
        return "ok"

    async def _settle(
        _service: str,
        *,
        settle_not_before_monotonic: float,
        source: str,
        window_deadline_at: str | None = None,
    ) -> None:
        captured.append(settle_not_before_monotonic)

    with patch(
        "scripts.model_manager.ui.controller.restart_window_ctl.invoke_propagation_settle_for_service",
        _settle,
    ):
        _run(
            lifecycle_with_restart_window(
                store,
                "git_integration_worker",
                "restart",
                _lifecycle,
            )
        )
    assert len(captured) == 1
    assert captured[0] <= during_lifecycle[0]


def test_lifecycle_exception_does_not_settle(store: RestartIntentStore) -> None:
    settle = AsyncMock()

    async def _fail() -> str:
        raise RuntimeError("boom")

    with patch(
        "scripts.model_manager.ui.controller.restart_window_ctl.invoke_propagation_settle_for_service",
        settle,
    ):
        with pytest.raises(RuntimeError, match="boom"):
            _run(
                lifecycle_with_restart_window(
                    store,
                    "mcp",
                    "restart",
                    _fail,
                )
            )
    settle.assert_not_awaited()


def test_lifecycle_cancelled_does_not_settle(store: RestartIntentStore) -> None:
    settle = AsyncMock()

    async def _cancel() -> str:
        raise asyncio.CancelledError

    with patch(
        "scripts.model_manager.ui.controller.restart_window_ctl.invoke_propagation_settle_for_service",
        settle,
    ):
        with pytest.raises(asyncio.CancelledError):
            _run(
                lifecycle_with_restart_window(
                    store,
                    "mcp",
                    "restart",
                    _cancel,
                )
            )
    settle.assert_not_awaited()


def test_lifecycle_clear_reason_discriminates_success(store: RestartIntentStore) -> None:
    cleared: list[str] = []

    async def _capture_clear(
        _store: RestartIntentStore, _service: str, *, reason: str
    ) -> list[Any]:
        cleared.append(reason)
        return []

    with patch(
        "scripts.model_manager.ui.controller.restart_window_ctl.clear_service_windows",
        _capture_clear,
    ), patch(
        "scripts.model_manager.ui.controller.restart_window_ctl.invoke_propagation_settle_for_service",
        AsyncMock(),
    ):
        _run(
            lifecycle_with_restart_window(
                store,
                "mcp",
                "restart",
                AsyncMock(return_value="ok"),
            )
        )
    assert cleared == ["lifecycle completed"]


def test_lifecycle_clear_reason_discriminates_failure(store: RestartIntentStore) -> None:
    cleared: list[str] = []

    async def _capture_clear(
        _store: RestartIntentStore, _service: str, *, reason: str
    ) -> list[Any]:
        cleared.append(reason)
        return []

    async def _fail() -> str:
        raise RuntimeError("boom")

    with patch(
        "scripts.model_manager.ui.controller.restart_window_ctl.clear_service_windows",
        _capture_clear,
    ), patch(
        "scripts.model_manager.ui.controller.restart_window_ctl.invoke_propagation_settle_for_service",
        AsyncMock(),
    ):
        with pytest.raises(RuntimeError):
            _run(
                lifecycle_with_restart_window(
                    store,
                    "mcp",
                    "restart",
                    _fail,
                )
            )
    assert cleared == ["lifecycle failed"]


def test_cross_service_settle_invocation_is_scoped(store: RestartIntentStore) -> None:
    settle = AsyncMock()
    with patch(
        "scripts.model_manager.ui.controller.restart_window_ctl.invoke_propagation_settle_for_service",
        settle,
    ):
        _run(
            lifecycle_with_restart_window(
                store,
                "stargate",
                "restart",
                AsyncMock(return_value="ok"),
            )
        )
    assert settle.await_args.args[0] == "stargate"
