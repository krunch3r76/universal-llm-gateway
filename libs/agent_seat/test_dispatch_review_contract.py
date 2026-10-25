"""Tests for review dispatch system appendix injection."""

from __future__ import annotations

import pytest

from agent_seat.dispatch_review_contract import review_dispatch_system_appendix


@pytest.mark.offline
def test_review_appendix_injected_for_code_review() -> None:
    appendix = review_dispatch_system_appendix("code-review")
    assert appendix is not None
    assert "REVIEW VERDICT CONTRACT" in appendix
    assert "CODE REVIEW INSPECTION" in appendix


@pytest.mark.offline
def test_review_appendix_none_for_freeform() -> None:
    assert review_dispatch_system_appendix("freeform") is None
    assert review_dispatch_system_appendix(None) is None
