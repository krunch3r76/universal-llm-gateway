"""Tests for served-artifact descriptor drift gate (arc 6637 AC4)."""

from __future__ import annotations

from services.git_integration_worker.relay.propagation_descriptor_drift import (
    check_descriptor_drift,
)


def _probe_payload(*, count: int) -> dict:
    return {"x_mcp_count": count}


def test_descriptor_drift_fatal_when_served_below_expected():
    def probe(service: str, *, code_ref: str) -> dict | None:
        counts = {
            "git_integration_worker": 8,
            "cortex_api": 46,
            "agent_bus": 17,
            "rag": 7,
        }
        return _probe_payload(count=counts[service])

    result = check_descriptor_drift(probe_fn=probe)
    assert any(
        "git_integration_worker: served x-mcp count 8 < expected 9" in msg
        for msg in result.fatal_messages
    )
    assert result.exit_code == 1


def test_descriptor_drift_warning_when_served_above_expected():
    def probe(service: str, *, code_ref: str) -> dict | None:
        counts = {
            "git_integration_worker": 9,
            "cortex_api": 51,
            "agent_bus": 17,
            "rag": 7,
        }
        return _probe_payload(count=counts[service])

    result = check_descriptor_drift(probe_fn=probe)
    assert result.fatal_messages == ()
    assert any(
        "cortex_api: served x-mcp count 51 > expected 50" in msg
        for msg in result.warning_messages
    )
    assert result.exit_code == 0


def test_descriptor_drift_warning_when_probe_unreachable():
    def probe(_service: str, *, code_ref: str) -> None:
        return None

    result = check_descriptor_drift(probe_fn=probe)
    assert result.fatal_messages == ()
    assert len(result.warning_messages) == 4
    assert all("probe unreachable" in msg for msg in result.warning_messages)
    assert result.exit_code == 0


