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


def test_resolve_prompt_preamble_injects_reasoning_posture_on_consult() -> None:
    text = resolve_prompt_preamble(
        handoff_contract="consult",
        prompt_preamble=None,
        inferred_contract=None,
    )
    assert "Use the `reasoning-posture` skill" in text


@pytest.mark.parametrize(
    "contract",
    ["implement", "pure-mechanical", "propagate", "execute", "answer"],
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
