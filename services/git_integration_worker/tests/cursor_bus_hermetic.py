"""Hermetic double for default ``CursorBusClient()`` construction in unit tests.

Production code often uses ``client or CursorBusClient()``; tests that omit
``client=`` / ``bus=`` must not POST to the live agent bus.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from services.git_integration_worker.cursor_bus import BusReplyResult

_DEFAULT_REPLY = BusReplyResult(status_code=200, body={})

# Import-bound names — patch each module that may default-construct in tests.
CURSOR_BUS_CLIENT_PATCH_TARGETS: tuple[str, ...] = (
    "services.git_integration_worker.cursor_sdk_park_resume",
    "services.git_integration_worker.cursor_sdk_park_http",
    "services.git_integration_worker.cursor_sdk_closeout.park_finalize",
    "services.git_integration_worker.routes.cursor_sdk",
)


def make_test_cursor_bus_client(*_args: Any, **_kwargs: Any) -> MagicMock:
    """Return an async-safe bus stand-in (never opens HTTP)."""
    client = MagicMock(name="CursorBusClient(test-double)")
    client.reply = AsyncMock(return_value=_DEFAULT_REPLY)
    client.terminate_dispatch = AsyncMock(return_value=_DEFAULT_REPLY)
    return client


def install_cursor_bus_hermetic(monkeypatch: Any) -> None:
    """Replace import-bound ``CursorBusClient`` constructors for one test."""
    factory = make_test_cursor_bus_client
    for module in CURSOR_BUS_CLIENT_PATCH_TARGETS:
        monkeypatch.setattr(f"{module}.CursorBusClient", factory)


def skip_cursor_bus_hermetic_for_node(nodeid: str) -> bool:
    """Tests that exercise the real ``CursorBusClient`` implementation."""
    return (
        "test_cursor_bus.py" in nodeid
        or "test_cursor_sdk_route.py" in nodeid
    )
