"""Lean-dispatch preamble harvest pointer (pytest matrix path)."""

from __future__ import annotations

import pytest

from agent_seat.dispatch_review_contract import UNSUPPORTED_ROUTE_PREFIX
from services.git_integration_worker.cursor_sdk_packet import (
    _LEAN_DISPATCH_PREAMBLE,
    resolve_prompt_preamble,
)

pytestmark = pytest.mark.offline


def test_lean_dispatch_preamble_harvest_pointer() -> None:
    assert "executive_envelope/effects_manifest" not in _LEAN_DISPATCH_PREAMBLE
    assert "sections=" not in _LEAN_DISPATCH_PREAMBLE
    assert "expected_read_sha256" not in _LEAN_DISPATCH_PREAMBLE
    assert "Selective reading of closeouts" in _LEAN_DISPATCH_PREAMBLE


@pytest.mark.parametrize(
    ("packet_text", "expect_coverage", "expect_unsupported"),
    [
        (
            "policy_route: freeform-ladder\nLeg 4 — Pre-land review\n",
            True,
            False,
        ),
        (
            "contract: freeform\nPlease run a code review.\n",
            False,
            True,
        ),
    ],
)
def test_freeform_admit_injects_review_appendix_from_packet_route(
    packet_text: str,
    expect_coverage: bool,
    expect_unsupported: bool,
) -> None:
    """AC2 — freeform-ladder packets get coverage contract via resolve_prompt_preamble."""
    preamble = resolve_prompt_preamble(
        handoff_contract="freeform",
        prompt_preamble=None,
        inferred_contract=None,
        existing_text=packet_text,
    )
    assert "DURABLE DELIVERABLE ROUTING" not in preamble
    if expect_coverage:
        assert "REVIEW COVERAGE CONTRACT" in preamble
        assert "REVIEW VERDICT CONTRACT" in preamble
    if expect_unsupported:
        assert UNSUPPORTED_ROUTE_PREFIX in preamble