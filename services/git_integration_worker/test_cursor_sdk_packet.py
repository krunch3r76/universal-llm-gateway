"""Tests for cursor-sdk packet preamble assembly."""

import pytest

from services.git_integration_worker.cursor_sdk_packet import resolve_prompt_preamble


def test_resolve_prompt_preamble_always_includes_deliverable_routing() -> None:
    text = resolve_prompt_preamble(
        handoff_contract=None,
        prompt_preamble=None,
        inferred_contract="light-bounded",
    )
    assert "DURABLE DELIVERABLE ROUTING" in text
    assert "/tmp/summaries/" in text
    assert 'path="cortex://' in text
    assert "workspaces://" in text
    assert "directory name is `cortex:`" in text
    assert "CHUNK, NEVER INLINE ONE GIANT WRITE" in text
    assert 'op="append"' in text


def test_resolve_prompt_preamble_implement_includes_routing_and_execute() -> None:
    text = resolve_prompt_preamble(
        handoff_contract="implement",
        prompt_preamble=None,
        inferred_contract=None,
    )
    assert "DURABLE DELIVERABLE ROUTING" in text
    assert "Execute this task NOW" in text


def test_resolve_prompt_preamble_preserves_custom_preamble() -> None:
    text = resolve_prompt_preamble(
        handoff_contract="consult",
        prompt_preamble="Custom lead preamble.",
        inferred_contract=None,
    )
    assert text.index("DURABLE DELIVERABLE ROUTING") < text.index(
        "Custom lead preamble."
    )


def test_resolve_prompt_preamble_injects_reasoning_posture_on_light_bounded() -> None:
    text = resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract=None,
    )
    assert text.count("Use the `reasoning-posture` skill") == 1
    assert text.count("Use the `ulg-for-llms` skill") == 1


def test_resolve_prompt_preamble_injects_reasoning_posture_on_consult() -> None:
    text = resolve_prompt_preamble(
        handoff_contract="consult",
        prompt_preamble=None,
        inferred_contract=None,
    )
    assert "Use the `reasoning-posture` skill" in text
    assert "Use the `ulg-for-llms` skill" in text
    assert "Use the `hypothesize-simulate` skill" in text


def test_resolve_prompt_preamble_hypothesize_simulate_judgment_contracts() -> None:
    """``light-bounded`` is the binding leg of a judgment split — it gets the fill."""
    consult = resolve_prompt_preamble(
        handoff_contract="consult",
        prompt_preamble=None,
        inferred_contract=None,
    )
    assert "Use the `hypothesize-simulate` skill" in consult
    light_bounded = resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract=None,
    )
    assert "Use the `hypothesize-simulate` skill" in light_bounded
    implement = resolve_prompt_preamble(
        handoff_contract="implement",
        prompt_preamble=None,
        inferred_contract=None,
    )
    assert "hypothesize-simulate" not in implement


@pytest.mark.parametrize(
    "contract",
    ["implement", "pure-mechanical", "propagate", "execute", "answer", "ask"],
)
def test_resolve_prompt_preamble_skips_reasoning_posture_on_mechanical_or_quick(
    contract: str,
) -> None:
    text = resolve_prompt_preamble(
        handoff_contract=contract,
        prompt_preamble=None,
        inferred_contract=None,
    )
    assert "reasoning-posture" not in text
    assert "ulg-for-llms" not in text


def test_resolve_prompt_preamble_reasoning_posture_idempotent_existing_text() -> None:
    existing = "Use the `reasoning-posture` skill — already in packet.\nDo the work."
    text = resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract=None,
        existing_text=existing,
    )
    assert text.count("Use the `reasoning-posture` skill") == 0
    combined = text + existing
    assert combined.count("Use the `reasoning-posture` skill") == 1


def test_resolve_prompt_preamble_reasoning_posture_idempotent_custom_preamble() -> None:
    custom = "Use the `reasoning-posture` skill\nCustom lead preamble."
    text = resolve_prompt_preamble(
        handoff_contract="consult",
        prompt_preamble=custom,
        inferred_contract=None,
    )
    assert text.count("Use the `reasoning-posture` skill") == 1


def test_conductor_seat_identity_block_only_under_three_condition_gate() -> None:
    dispatch_id = "conductor-dispatch-abc123"
    gated = resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        lane_branch="cursor-sdk/lane-7414",
        dispatch_id=dispatch_id,
        has_packet_path=True,
    )
    assert "CONDUCTOR SEAT IDENTITY" in gated
    assert dispatch_id in gated
    assert "nest_under=" + dispatch_id in gated
    assert "Independent dispatch" in gated
    assert "CURSOR_SOURCE_REF_IN_FLIGHT" in gated

    assert "CONDUCTOR SEAT IDENTITY" not in resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        dispatch_id=dispatch_id,
        has_packet_path=False,
        existing_text="TYPE: DIRECTIVE\nscope: investigate only\n",
    )
    assert "CONDUCTOR SEAT IDENTITY" not in resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract=None,
        lane=None,
        dispatch_id=dispatch_id,
        has_packet_path=True,
    )
    assert "CONDUCTOR SEAT IDENTITY" not in resolve_prompt_preamble(
        handoff_contract="implement",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        dispatch_id=dispatch_id,
        has_packet_path=True,
    )


def test_conductor_seat_identity_uses_req_dispatch_id() -> None:
    dispatch_id = "98836b38"
    text = resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        dispatch_id=dispatch_id,
        has_packet_path=True,
    )
    assert f"dispatch_id is {dispatch_id}" in text
    assert f"nest_under={dispatch_id}" in text


_CONDUCTOR_USE_LINE = (
    "Use the conductor skill — nest specialists; ¬ hand-code mechanical G-rows; "
    "cost tier from this skill."
)


def test_conductor_seat_identity_fires_on_message_body_with_conductor_marker() -> None:
    dispatch_id = "auto-abc123def456"
    text = resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        lane_branch="cursor-sdk/lane-7420",
        dispatch_id=dispatch_id,
        has_packet_path=False,
        existing_text=f"TYPE: DIRECTIVE\n{_CONDUCTOR_USE_LINE}\n",
    )
    assert "CONDUCTOR SEAT IDENTITY" in text
    assert dispatch_id in text
    assert f"nest_under={dispatch_id}" in text


def test_conductor_seat_identity_absent_on_message_body_without_conductor_marker() -> None:
    dispatch_id = "auto-abc123def456"
    text = resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        dispatch_id=dispatch_id,
        has_packet_path=False,
        existing_text="TYPE: DIRECTIVE\ncontract: investigate\nscope: repo\n",
    )
    assert "CONDUCTOR SEAT IDENTITY" not in text


def test_conductor_run_to_completion_present_under_three_condition_gate() -> None:
    dispatch_id = "conductor-dispatch-abc123"
    gated = resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        lane_branch="cursor-sdk/lane-7414",
        dispatch_id=dispatch_id,
        has_packet_path=True,
    )
    assert "CONDUCTOR RUN TO COMPLETION" in gated
    assert "nest_under=" + dispatch_id in gated

    assert "CONDUCTOR RUN TO COMPLETION" not in resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        dispatch_id=dispatch_id,
        has_packet_path=False,
        existing_text="TYPE: DIRECTIVE\nscope: investigate only\n",
    )
    assert "CONDUCTOR RUN TO COMPLETION" not in resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract=None,
        lane=None,
        dispatch_id=dispatch_id,
        has_packet_path=True,
    )
    assert "CONDUCTOR RUN TO COMPLETION" not in resolve_prompt_preamble(
        handoff_contract="implement",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        dispatch_id=dispatch_id,
        has_packet_path=True,
    )


def test_conductor_run_to_completion_fires_on_message_body_marker() -> None:
    dispatch_id = "auto-abc123def456"
    text = resolve_prompt_preamble(
        handoff_contract="light-bounded",
        prompt_preamble=None,
        inferred_contract=None,
        lane="B",
        lane_branch="cursor-sdk/lane-7420",
        dispatch_id=dispatch_id,
        has_packet_path=False,
        existing_text=f"TYPE: DIRECTIVE\n{_CONDUCTOR_USE_LINE}\n",
    )
    assert "CONDUCTOR RUN TO COMPLETION" in text
    assert "nest_under=" + dispatch_id in text
