"""Autouse isolation for package-level ``test_*.py`` modules in agent_bus_store."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_PIN_ENV = "PYTEST_EVENTS_INGEST_SOCK_PIN"


def _pin_events_ingest_sock_off_live() -> None:
    """Point ``EVENTS_INGEST_SOCK`` at a fresh tmp path before agent_bus_store imports.

    ``agent_bus_store.events.publisher`` and ``mcp_events`` read the variable at
    import time and start a sender thread that connects at once. pytest loads
    this conftest before any test module under it, and this module imports no
    agent_bus_store code at module level, so the pin is in place first. The
    same pin lives in ``libs/bus_watch/conftest.py``; a second call is a no-op.
    """
    pinned = os.environ.get(_PIN_ENV)
    if pinned and os.environ.get("EVENTS_INGEST_SOCK") == pinned:
        return
    pin_dir = tempfile.mkdtemp(prefix="pytest-events-ingest-")
    pinned = os.path.join(pin_dir, "events.sock")
    os.environ[_PIN_ENV] = pinned
    os.environ["EVENTS_INGEST_SOCK"] = pinned


_pin_events_ingest_sock_off_live()


@pytest.fixture(autouse=True)
def _isolate_agent_bus_db_package_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_bus_store.testing.wake_cursor_test_support import (
        isolate_agent_bus_db_for_pytest,
        isolate_endpoint_wake_event_emit_for_pytest,
    )

    isolate_agent_bus_db_for_pytest(monkeypatch, tmp_path)
    isolate_endpoint_wake_event_emit_for_pytest(monkeypatch, tmp_path)
