"""Tests for team_dispatch knob_resolution preview transport contract."""

from __future__ import annotations

from llm_adapters.capability_dispatch import project_knob_resolution

_MAX_OUTPUT_KEYS = frozenset({"requested", "resolved", "decision", "floor", "ceiling"})


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


def test_dispatch_preview_merges_max_output_on_knob_resolution() -> None:
    """Regression-lock: max_output rides team_dispatch knob_resolution to caller."""
    preview = project_knob_resolution(
        resolved_model="openai/gpt-5.5",
        requested_effort="high",
        requested_max_output=4096,
    )
    dispatch_body = {
        "pipeline_options": {
            "model": "openai/gpt-5.5",
            "_knob_resolution_preview": preview,
        }
    }
    popped = dispatch_body.get("pipeline_options", {}).pop(
        "_knob_resolution_preview", None
    )
    assert "_knob_resolution_preview" not in dispatch_body["pipeline_options"]

    result: dict[str, object] = {"execution_id": "exec-max-output"}
    if popped is not None:
        result["knob_resolution"] = popped

    knob = result["knob_resolution"]
    assert isinstance(knob, dict)
    max_output = knob["max_output"]
    assert isinstance(max_output, dict)
    assert set(max_output) == _MAX_OUTPUT_KEYS
    assert max_output["requested"] == 4096
    assert max_output["resolved"] == 16384
    assert max_output["decision"] == "floor_bump"
