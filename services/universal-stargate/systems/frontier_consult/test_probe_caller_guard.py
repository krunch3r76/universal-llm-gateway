"""Unit tests for MCP probe caller admission guard (cost-overhaul Wave 0.3)."""

from __future__ import annotations

import pytest

from systems.frontier_consult.admission import (
    FrontierEndpointError,
    enforce_team_dispatch_generate_admit,
)
from systems.frontier_consult.probe_caller_guard import is_mcp_probe_caller


def test_is_mcp_probe_caller_matches_ladder_ids() -> None:
    assert is_mcp_probe_caller("mcp-l0-probe")
    assert is_mcp_probe_caller("mcp-l3-probe")
    assert is_mcp_probe_caller("mcp-trace-matrix")
    assert is_mcp_probe_caller("MCP-L1-PROBE")
    assert not is_mcp_probe_caller(None)
    assert not is_mcp_probe_caller("")
    assert not is_mcp_probe_caller("claude-cursor")
    assert not is_mcp_probe_caller("mcp-server")


def test_probe_caller_rejected_on_reviewer() -> None:
    with pytest.raises(FrontierEndpointError) as exc_info:
        enforce_team_dispatch_generate_admit(
            "reviewer",
            request_id="req-probe",
            caller_agent="mcp-l0-probe",
        )
    assert exc_info.value.code == "probe_reviewer_forbidden"
    assert exc_info.value.field == "caller_agent"


def test_probe_caller_allowed_on_artisan() -> None:
    enforce_team_dispatch_generate_admit(
        "artisan",
        request_id="req-probe-ok",
        caller_agent="mcp-l3-probe",
    )


def test_reviewer_without_probe_caller_still_admitted() -> None:
    enforce_team_dispatch_generate_admit(
        "reviewer",
        request_id="req-review",
        caller_agent="claude-cursor",
    )
