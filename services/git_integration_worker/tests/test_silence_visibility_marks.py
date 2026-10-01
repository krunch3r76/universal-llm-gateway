"""Hermetic marks for silence-family victim classes (arc 6929 L5)."""

from __future__ import annotations

from claude_bundles.cdp_model_endpoint import result_from_snapshot


def test_run_cdp_generate_proof_weekly_limit_banner_not_a_seat() -> None:
    """Weekly-limit harvest ⇒ ok=False, stall_stage=weekly_limit, banner mark."""
    result = result_from_snapshot(
        snapshot={
            "status": "completed",
            "completion_phase": "terminal",
            "body": "You've hit your weekly limit. Try again next week.",
            "attested_model": "Model: Opus",
            "harvest_provenance": "chat",
        },
        execution_id="disp-wl",
        satellite_execution_id="sat-wl",
        prompt_uri="cortex://notes/system/threads/wl-prompt.md",
        picker_model="opus-5",
    )
    assert result is not None
    assert result.ok is False
    assert result.stall_stage == "weekly_limit"
    assert result.extras.get("mark") == "banner_not_a_seat"
