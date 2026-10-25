"""Shared fixtures for controller offline tests."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_MCP_SERVER = Path(__file__).resolve().parents[5] / "services" / "mcp-server"
if _MCP_SERVER.is_dir() and str(_MCP_SERVER) not in sys.path:
    sys.path.insert(0, str(_MCP_SERVER))


def stub_process_live_probe(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace ``probe_process_live`` with a recorder (no TCP/UDS health GET)."""
    calls: list[str] = []

    def _probe(service: str) -> dict[str, Any]:
        calls.append(service)
        return {"probe_reachable": False, "service": service}

    monkeypatch.setattr(
        "services.git_integration_worker.relay.propagation_probe.probe_process_live",
        _probe,
    )
    return calls


@pytest.fixture(autouse=True)
def _stub_process_live_probe_autouse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default-off live health GET for any test that mints activation validation."""
    stub_process_live_probe(monkeypatch)
