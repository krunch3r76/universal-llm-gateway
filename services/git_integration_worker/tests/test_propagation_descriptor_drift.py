"""Tests for served-artifact descriptor drift gate (arc 6637 AC4)."""

from __future__ import annotations

import pytest

from services.git_integration_worker.cursor_auto.propagation_descriptor_drift import (
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
    assert any("git_integration_worker: served x-mcp count 8 < expected 9" in msg for msg in result.fatal_messages)
    assert result.exit_code == 1


def test_descriptor_drift_warning_when_served_above_expected():
    def probe(service: str, *, code_ref: str) -> dict | None:
        counts = {
            "git_integration_worker": 9,
            "cortex_api": 47,
            "agent_bus": 17,
            "rag": 7,
        }
        return _probe_payload(count=counts[service])

    result = check_descriptor_drift(probe_fn=probe)
    assert result.fatal_messages == ()
    assert any("cortex_api: served x-mcp count 47 > expected 46" in msg for msg in result.warning_messages)
    assert result.exit_code == 0


def test_descriptor_drift_fatal_when_probe_unreachable():
    def probe(_service: str, *, code_ref: str) -> None:
        return None

    result = check_descriptor_drift(probe_fn=probe)
    assert len(result.fatal_messages) == 4
    assert all("probe unreachable" in msg for msg in result.fatal_messages)


@pytest.mark.offline
def test_fleet_check_invokes_descriptor_drift_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openapi_mcp.codegen import ManifestCheckResult

    from scripts import openapi_mcp_codegen as codegen

    calls: list[str] = []

    def _track(**_kwargs):
        calls.append("descriptor_drift")
        from services.git_integration_worker.cursor_auto.propagation_descriptor_drift import (
            DescriptorDriftResult,
        )

        return DescriptorDriftResult(fatal_messages=(), warning_messages=())

    monkeypatch.setattr(
        codegen,
        "_check_service_detailed",
        lambda _s: ManifestCheckResult((), ()),
    )
    monkeypatch.setattr(
        codegen,
        "check_tier_m_manifest_coverage",
        lambda: type(
            "R",
            (),
            {"check_result": ManifestCheckResult((), ())},
        )(),
    )
    from services.git_integration_worker.cursor_auto import (
        propagation_descriptor_drift,
        propagation_served_binding_drift,
    )

    monkeypatch.setattr(propagation_descriptor_drift, "check_descriptor_drift", _track)
    monkeypatch.setattr(
        propagation_served_binding_drift,
        "check_served_binding_drift",
        lambda *_a, **_k: ManifestCheckResult((), ()),
    )
    codegen._run_check(["cortex"])
    assert calls == ["descriptor_drift"]
