"""Tests for cursor-sdk packet preamble assembly."""

from services.git_integration_worker.cursor_sdk_packet import resolve_prompt_preamble


def test_resolve_prompt_preamble_always_includes_deliverable_routing() -> None:
    text = resolve_prompt_preamble(
        handoff_contract=None,
        prompt_preamble=None,
        inferred_contract="light-bounded",
    )
    assert "DURABLE DELIVERABLE ROUTING" in text
    assert "/tmp/summaries/" in text
    assert 'fs(sandbox="cortex"' in text
    assert 'fs(sandbox="workspaces"' in text
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
