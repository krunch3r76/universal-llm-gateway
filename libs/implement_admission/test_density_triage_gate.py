"""Tests for implement-lane density_triage gate vocabulary."""

from __future__ import annotations

import pytest

from implement_admission.density_triage_gate import (
    IMPLEMENT_GATE_TRIAGE,
    format_implement_gate_triage_catalog,
    format_implement_triage_unknown_reason,
)


@pytest.mark.offline
def test_implement_gate_triage_values() -> None:
    assert IMPLEMENT_GATE_TRIAGE == frozenset(
        {"mechanical", "judgment_required", "recon_pending"}
    )


@pytest.mark.offline
def test_catalog_lists_all_gate_values_with_effects() -> None:
    catalog = format_implement_gate_triage_catalog()
    assert "mechanical (bypass implement-ready gates)" in catalog
    assert "judgment_required" in catalog
    assert "recon_pending" in catalog


@pytest.mark.offline
@pytest.mark.parametrize("triage", [None, "", "bogus", "dispatch_surface"])
def test_unknown_reason_is_self_describing(triage: str | None) -> None:
    reason = format_implement_triage_unknown_reason("todo:example", triage)
    assert "todo:example" in reason
    assert "mechanical (bypass implement-ready gates)" in reason
    assert "judgment_required" in reason
    assert "recon_pending" in reason
