"""Bridge read-idle helpers and SDK client timeout wiring."""

from __future__ import annotations

import httpx

from services.git_integration_worker.cursor_sdk_bridge_read_idle import (
    BRIDGE_READ_IDLE_MARGIN_S,
    bridge_read_timeout_for_idle,
)
from services.git_integration_worker.routes.cursor_sdk import (
    _SDK_CLIENT_TIMEOUT,
    _sdk_client_read_timeout,
)


def test_sdk_client_read_timeout_default_is_numeric() -> None:
    value = _sdk_client_read_timeout()
    assert isinstance(value, float)
    assert value > 0


def test_sdk_client_timeout_read_is_numeric() -> None:
    assert isinstance(_SDK_CLIENT_TIMEOUT.read, (int, float))


def test_bridge_read_timeout_for_idle_returns_full_timeout() -> None:
    timeout = bridge_read_timeout_for_idle(idle_budget_s=1800.0)
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read == 1800.0 + BRIDGE_READ_IDLE_MARGIN_S
