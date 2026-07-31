"""Pytest configuration for git-integration-worker tests."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(autouse=True)
def _cursor_auto_admit_bus_stubs(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Keep cursor-auto admit tests hermetic after thread-status gate landed."""
    if "cursor_auto" not in request.node.nodeid:
        yield
        return
    from services.git_integration_worker.cursor_auto import admit_gates
    import services.git_integration_worker.cursor_sdk_events as cursor_sdk_events

    monkeypatch.setattr(
        admit_gates,
        "fetch_thread_status",
        AsyncMock(return_value="active"),
    )
    monkeypatch.setattr(
        admit_gates,
        "fetch_thread_turns",
        AsyncMock(return_value=[]),
    )
    prior_publisher = cursor_sdk_events._uds_publisher
    cursor_sdk_events._uds_publisher = None
    yield
    cursor_sdk_events._uds_publisher = prior_publisher
