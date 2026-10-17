"""A7 — scan for ungated Anthropic API egress outside the credit gate (PLAN r2 § A7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from libs.anthropic_credit.egress_inventory import (
    _ALLOWLIST,
    _LINE_ALLOWLIST,
    _SERVED_ROOT_ALLOWLIST,
    render,
    scan,
)

pytestmark = pytest.mark.offline

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_egress_inventory_no_ungated_api_anthropic_or_resolve_calls() -> None:
    """Break covered: new direct Messages egress or resolve_llm_adapter('anthropic') bypasses gate."""
    report = scan(_REPO_ROOT)
    if not report.ok:
        pytest.fail(render(report))

    assert report.matched_allowlisted_tracked, (
        "expected at least one known allowlisted hit in the tree"
    )
    for rel in report.matched_allowlisted_tracked:
        assert rel in _ALLOWLIST


__all__ = [
    "_ALLOWLIST",
    "_LINE_ALLOWLIST",
    "_SERVED_ROOT_ALLOWLIST",
]
