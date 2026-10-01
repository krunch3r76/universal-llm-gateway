"""Tests for tier-M manifest coverage drift (G4)."""

from __future__ import annotations

import pytest

from openapi_mcp.tier_m_coverage import (
    check_tier_m_manifest_coverage,
    collect_served_tool_ops,
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
def test_absent_local_only_surface_warns_instead_of_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean checkout has no gitignored local-only tools; the gate must still pass."""
    served = {k: v for k, v in collect_served_tool_ops().items() if k != "email"}
    monkeypatch.setattr(
        "openapi_mcp.tier_m_coverage.collect_served_tool_ops",
        lambda: served,
    )

    report = check_tier_m_manifest_coverage()
    assert not report.fatal_messages
    assert any(
        "tier-M coverage skipped for local-only tool 'email'" in msg
        for msg in report.warning_messages
    )


@pytest.mark.offline
def test_current_manifest_drift_state_is_reported() -> None:
    report = check_tier_m_manifest_coverage()
    assert report.manifest_row_count == 9
    assert report.served_op_count > 0
    assert not report.fatal_messages


