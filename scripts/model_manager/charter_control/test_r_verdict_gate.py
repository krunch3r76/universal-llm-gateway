"""Offline tests for merits-vs-scope R verdict parsing (a:26595)."""

from __future__ import annotations

import pytest

from scripts.model_manager.charter_control.r_verdict_gate import (
    RGateAction,
    ConsultProvenance,
    consult_provenance_from_r_admit,
    format_consult_provenance_md,
    parse_r_verdict,
    parse_r_verdict_with_independence,
)


@pytest.mark.offline
def test_parse_r_verdict_prefers_bold_merits_over_scope_ratify() -> None:
    """5975-w13 harvest shape: Scope RATIFY precedes bold Merits amendments."""
    body = (
        "1. **Scope check:** RATIFY — Question/OOS/detent/gate pinned; "
        "self-referential harvest is depth-1 design, not SCOPE-DRIFT.\n"
        "2. **Merits:** ADMIT_WITH_AMENDMENTS — authorize harvest + four-field "
        "write on cdp/ path.\n"
    )
    parsed = parse_r_verdict(body)
    assert parsed.verdict == "ADMIT_WITH_AMENDMENTS"
    assert parsed.action is RGateAction.AMENDMENTS_REQUIRED
    prov = consult_provenance_from_r_admit(
        consult_thread="agent-bus:5975",
        harvest_text=body,
        consultant_model="claude-opus-5",
        consultant_effort="high",
        consultant_substrate="cdp",
    )
    assert prov is not None
    assert prov.verdict == "ADMIT_WITH_AMENDMENTS"


@pytest.mark.offline
def test_parse_r_verdict_subject_fallback_skips_scope_check_token() -> None:
    """When Merits uses disposition wording, still ignore Scope RATIFY."""
    body = (
        "Scope check: RATIFY — bounded.\n"
        "Merits disposition — ADMIT_WITH_AMENDMENTS\n"
    )
    parsed = parse_r_verdict(body)
    assert parsed.verdict == "ADMIT_WITH_AMENDMENTS"
    assert parsed.action is RGateAction.AMENDMENTS_REQUIRED


@pytest.mark.offline
def test_parse_r_verdict_scope_only_still_advances_via_fallback() -> None:
    """A body with only a Scope RATIFY (no Merits) keeps subject-fallback ADMIT path."""
    parsed = parse_r_verdict("Scope check: RATIFY — ok")
    # Scope-check context is skipped; no remaining token ⇒ unparseable/blocked.
    assert parsed.verdict is None
    assert parsed.action is RGateAction.BLOCKED


@pytest.mark.offline
def test_parse_r_verdict_with_independence_blocks_unmeasured_family() -> None:
    body = "Merits: ADMIT — families measured."
    blocked = parse_r_verdict_with_independence(
        body, r_family="unknown", implement_family="anthropic"
    )
    assert blocked.action is RGateAction.BLOCKED
    assert blocked.reason == "unmeasured_family_r_pre_check"
    same = parse_r_verdict_with_independence(
        body, r_family="anthropic", implement_family="anthropic"
    )
    assert same.action is RGateAction.BLOCKED
    assert same.reason == "same_family_r_pre_check_only"
    ok = parse_r_verdict_with_independence(
        body, r_family="xai", implement_family="anthropic"
    )
    assert ok.action is RGateAction.ADVANCE


@pytest.mark.offline
def test_format_consult_provenance_md_emits_model_and_effort() -> None:
    prov = ConsultProvenance(
        consult_thread="agent-bus:1",
        verdict="ADMIT",
        consultant_model="claude-fable-5-1",
        consultant_effort="high",
        consultant_substrate="web-anthropic",
    )
    md = format_consult_provenance_md(prov)
    assert "- consult_thread: agent-bus:1" in md
    assert "- verdict: ADMIT" in md
    assert "- consultant_model: claude-fable-5-1" in md
    assert "- consultant_effort: high" in md
    assert "- consultant_substrate: web-anthropic" in md
    assert "consultant_family" not in md

    unmeasured = ConsultProvenance(
        consult_thread="agent-bus:2",
        verdict="ADMIT",
        consultant_model="composer-2.5",
        consultant_effort=None,
        consultant_substrate="cursor",
    )
    md2 = format_consult_provenance_md(unmeasured)
    assert "- consultant_effort: unmeasured" in md2
