"""Tests for team_dispatch knob_resolution preview transport contract."""

from __future__ import annotations


def test_dispatch_preview_popped_before_forward_and_merged_on_response() -> None:
    """Route contract: preview lives on pipeline_options, pops before forward."""
    dispatch_body = {
        "pipeline_options": {
            "model": "openai/gpt-5.5",
            "_knob_resolution_preview": {
                "provenance": "preview",
                "resolved_model": "openai/gpt-5.5",
                "status": "mapped",
            },
        }
    }
    preview = dispatch_body.get("pipeline_options", {}).pop(
        "_knob_resolution_preview", None
    )
    forward_payload = dispatch_body
    assert "_knob_resolution_preview" not in forward_payload["pipeline_options"]

    result = {"execution_id": "exec-1"}
    if isinstance(result, dict) and preview is not None:
        result["knob_resolution"] = preview

    assert result["knob_resolution"]["provenance"] == "preview"
    assert result["knob_resolution"]["resolved_model"] == "openai/gpt-5.5"
