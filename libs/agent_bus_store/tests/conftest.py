"""Autouse isolation for agent_bus_store tests under ``tests/``."""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_bus_store.testing.wake_cursor_test_support import (
    isolate_agent_bus_db_for_pytest,
    isolate_endpoint_wake_event_emit_for_pytest,
)


@pytest.fixture(autouse=True)
def _isolate_agent_bus_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolate_agent_bus_db_for_pytest(monkeypatch, tmp_path)
    isolate_endpoint_wake_event_emit_for_pytest(monkeypatch, tmp_path)

