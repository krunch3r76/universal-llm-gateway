"""Tests for tier-M manifest coverage drift (G4)."""

from __future__ import annotations

import pytest

from openapi_mcp.codegen import ManifestCheckResult
from openapi_mcp.tier_m_coverage import (
    TierMDriftReport,
    check_tier_m_manifest_coverage,
    collect_served_tool_ops,
)


@pytest.mark.offline
def test_manifest_row_without_served_op_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.git_integration_worker.cursor_auto import tier_m_manifest as tm

    fake_row = tm.ManifestRow(
        tool="email",
        op="nonexistent_op",
        allowed=True,
        idempotence="idempotent",
        authority="life",
        note="test-only phantom row",
    )
    monkeypatch.setattr(tm, "DEFAULT_MANIFEST", (fake_row,))

    report = check_tier_m_manifest_coverage()
    assert report.check_result.exit_code == 1
    assert any(
        "FATAL: tier-M manifest row 'email.nonexistent_op' has no served operation"
        in msg
        for msg in report.fatal_messages
    )


@pytest.mark.offline
def test_served_op_without_manifest_row_is_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    served = collect_served_tool_ops()
    extra = dict(served)
    extra["cortex"] = frozenset(set(served["cortex"]) | {"phantom_op"})
    monkeypatch.setattr(
        "openapi_mcp.tier_m_coverage.collect_served_tool_ops",
        lambda: extra,
    )

    report = check_tier_m_manifest_coverage()
    assert any(
        "WARNING: served tier-M-eligible op cortex.phantom_op has no manifest row"
        in msg
        for msg in report.warning_messages
    )


@pytest.mark.offline
def test_current_manifest_drift_state_is_reported() -> None:
    report = check_tier_m_manifest_coverage()
    assert report.manifest_row_count == 9
    assert report.served_op_count > 0
    assert not report.fatal_messages


@pytest.mark.offline
def test_fleet_check_invokes_tier_m_coverage_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import openapi_mcp_codegen as codegen

    calls: list[str] = []

    def _track() -> TierMDriftReport:
        calls.append("tier_m")
        return check_tier_m_manifest_coverage()

    monkeypatch.setattr(codegen, "check_tier_m_manifest_coverage", _track)
    codegen._run_check(["cortex"])
    assert calls == ["tier_m"]
