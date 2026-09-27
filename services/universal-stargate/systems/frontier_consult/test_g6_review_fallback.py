"""Rebase a fable review leg onto the execution id the conductor is polling."""

from __future__ import annotations

from claude_bundles.cdp_model_endpoint import CdpGenerateResult

from systems.frontier_consult.cdp_generate_worker import (
    note_unadopted_review_fallback,
    rebase_review_fallback,
)


def _result(**kwargs: object) -> CdpGenerateResult:
    base = dict(
        ok=False,
        body="",
        execution_id="exec-opus",
        satellite_execution_id=None,
        prompt_uri="cortex://prompt",
        picker_model="opus",
    )
    base.update(kwargs)
    return CdpGenerateResult(**base)  # type: ignore[arg-type]


def test_rebase_review_fallback_keeps_original_execution_id() -> None:
    fallback = _result(
        ok=True,
        body="VERDICT: approve",
        execution_id="exec-opus:fable",
        satellite_execution_id="sat-fable",
        picker_model="fable",
        stall_stage=None,
    )
    rebased = rebase_review_fallback(
        fallback,
        original_execution_id="exec-opus",
        original_model_id="cdp/opus-5",
        fallback_model="cdp/fable",
        primary_satellite_execution_id="sat-opus",
    )
    assert rebased.execution_id == "exec-opus"
    assert rebased.body == "VERDICT: approve"
    assert rebased.extras["review_fallback_from"] == "cdp/opus-5"
    assert rebased.extras["review_fallback_model"] == "cdp/fable"
    assert rebased.extras["review_fallback_execution_id"] == "exec-opus:fable"
    assert rebased.extras["review_primary_satellite_execution_id"] == "sat-opus"


def test_empty_fable_body_is_not_adopted() -> None:
    primary = _result(
        stall_stage="completed_without_proof",
        error="no proof",
        satellite_execution_id="sat-opus",
    )
    kept = note_unadopted_review_fallback(
        primary,
        fallback_model="cdp/fable",
        fallback_execution_id="exec-opus:fable",
        stall_stage="completed_without_proof",
    )
    assert kept.execution_id == "exec-opus"
    assert kept.stall_stage == "completed_without_proof"
    assert kept.extras["review_fallback_attempted"] is True
    assert kept.body == ""
