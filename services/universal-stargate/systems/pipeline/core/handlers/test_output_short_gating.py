"""Phase 5 output_short heuristic gating tests — dispatch-surface-split.

Covers:
- D6: output_short heuristic is suppressed when output_contract="thread"
- D7: output_short heuristic still fires when output_contract="inline"

The gating is in frontier_dispatch_observability._emit_output_short:

    short_hint = (
        _emit_output_short(...)
        if output_contract == "inline"
        else None  # bus-mode: pipeline result is action-narration by design
    )
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from systems.pipeline.core.handlers.frontier_dispatch_observability import (
    emit_post_loop_observability,
)


def _make_result(
    *,
    output_tokens: int = 10,
    provider: str = "openai",
    tool_calls_made: int = 0,
    finish_reason: str = "stop",
    block_reason: str | None = None,
    content: str | None = "ok",
    reasoning: Any = None,
) -> SimpleNamespace:
    """Minimal NativeLoopResult stub for observability module tests."""
    return SimpleNamespace(
        usage={"output_tokens": output_tokens},
        provider=provider,
        tool_calls_made=tool_calls_made,
        finish_reason=finish_reason,
        block_reason=block_reason,
        content=content,
        reasoning=reasoning,
    )


def _make_context(output_contract: str) -> SimpleNamespace:
    return SimpleNamespace(
        options={"output_contract": output_contract},
        execution_id="exec-phase5-test",
    )


def _null_publish(event: Any) -> None:
    pass


# ---------------------------------------------------------------------------
# D6 — output_short suppressed for op="to_thread" (output_contract="thread")
# ---------------------------------------------------------------------------


def test_d6_output_short_suppressed_for_to_thread() -> None:
    """Bus-mode dispatch: output_short heuristic must NOT fire.

    op="to_thread" results are action-narration by design (the reply content
    lives on the agent-bus thread, not in the pipeline result).  A short
    completion_tokens count is expected and must not be flagged.
    """
    context = _make_context("thread")
    result = _make_result(output_tokens=10)  # well below SHORT_OUTPUT_TOKEN_THRESHOLD

    hints = emit_post_loop_observability(
        context=context,
        publish=_null_publish,
        agent="orion",
        boot_level="team",
        model="openai/gpt-5.4",
        result=result,
    )

    output_short_hints = [h for h in hints if h.get("type") == "output_short"]
    assert len(output_short_hints) == 0, (
        f"output_short heuristic fired on op='to_thread' dispatch — "
        f"surface split is incomplete.  hints={hints}"
    )


def test_d6_suppression_holds_for_zero_tokens() -> None:
    """Edge case: even 0 output tokens on to_thread should not produce a hint."""
    context = _make_context("thread")
    result = _make_result(output_tokens=0)

    hints = emit_post_loop_observability(
        context=context,
        publish=_null_publish,
        agent="orion",
        boot_level="team",
        model="openai/gpt-5.4",
        result=result,
    )

    assert all(h.get("type") != "output_short" for h in hints)


# ---------------------------------------------------------------------------
# D7 — output_short still fires for op="generate" with short content
# ---------------------------------------------------------------------------


def test_d7_output_short_fires_for_generate_short_content() -> None:
    """Direct-mode dispatch: output_short heuristic MUST fire on short output.

    op="generate" results are inline content.  Low token count + stop finish
    is the original failure signal that triggered the surface split.  The
    heuristic must remain active on the "inline" path.
    """
    context = _make_context("inline")
    result = _make_result(
        output_tokens=10,  # below SHORT_OUTPUT_TOKEN_THRESHOLD (500)
        finish_reason="stop",
        tool_calls_made=0,
    )

    published: list[Any] = []

    def _capture_publish(event: Any) -> None:
        published.append(event)

    hints = emit_post_loop_observability(
        context=context,
        publish=_capture_publish,
        agent="orion",
        boot_level="team",
        model="openai/gpt-5.4",
        result=result,
    )

    output_short_hints = [h for h in hints if h.get("type") == "output_short"]
    assert len(output_short_hints) == 1, (
        f"output_short heuristic did not fire on op='generate' with short output.  "
        f"hints={hints}"
    )
    assert output_short_hints[0]["output_tokens"] == 10

    # Verify the event was also published (not just returned as a hint)
    published_signals = [
        getattr(e, "signal", None) for e in published if hasattr(e, "signal")
    ]
    assert any("output.short" in (s or "") for s in published_signals), (
        f"pipeline.frontier.dispatch.output.short event not emitted.  "
        f"published signals: {published_signals}"
    )


def test_d7_persona_free_dispatch_short_output_suppressed() -> None:
    """Persona-free dispatches (boot_level="none") bypass the heuristic.

    detect_output_short gates on boot_level in {team, full}.  This test
    confirms the gate is respected even on the inline path.
    """
    context = _make_context("inline")
    result = _make_result(output_tokens=10)

    hints = emit_post_loop_observability(
        context=context,
        publish=_null_publish,
        agent=None,
        boot_level="none",  # persona-free dispatch
        model="openai/gpt-5.4",
        result=result,
    )

    assert all(h.get("type") != "output_short" for h in hints)
