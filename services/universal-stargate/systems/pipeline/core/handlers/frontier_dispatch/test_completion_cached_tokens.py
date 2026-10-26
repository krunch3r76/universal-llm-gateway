"""build_dispatch_output cached_tokens plumbing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from agent_seat.native_loop import NativeLoopResult

from systems.pipeline.core.handlers.frontier_dispatch.completion import (
    build_dispatch_output,
)


def _minimal_build_args(*, usage: dict[str, Any]) -> tuple[Any, ...]:
    result = NativeLoopResult(
        content="done",
        reasoning=None,
        usage=usage,
        tool_calls=[],
        turns_used=1,
        exhausted=False,
        cancelled=False,
        provider="openai",
        raw={},
    )
    outcome = SimpleNamespace(
        result=result,
        latency_ms=10.0,
        finish_reason="stop",
        block_reason=None,
        exhaustion_summary=None,
    )
    context = SimpleNamespace(execution_id="exec-1", options={})
    step = SimpleNamespace(id="respond", name="respond")
    admission = SimpleNamespace(
        agent="artisan",
        model="openai/gpt-5",
        model_entity_id="model:gpt-5",
        user_prompt="hi",
        hydration_meta=None,
        publish=lambda _e: None,
    )
    return context, step, admission, outcome, "system prompt"


def test_build_dispatch_output_populates_cached_tokens() -> None:
    """StepOutput.cached_tokens mirrors result.usage cached_tokens key."""
    args = _minimal_build_args(
        usage={"input_tokens": 100, "output_tokens": 20, "cached_tokens": 40}
    )
    output = build_dispatch_output(*args)
    assert output.cached_tokens == 40
    assert output.json is not None
    assert output.json["cached_tokens"] == 40


def test_build_dispatch_output_omits_cached_tokens_when_absent() -> None:
    """StepOutput and json omit cached_tokens when usage lacks the field."""
    args = _minimal_build_args(usage={"input_tokens": 100, "output_tokens": 20})
    output = build_dispatch_output(*args)
    assert output.cached_tokens is None
    assert output.json is not None
    assert "cached_tokens" not in output.json


def test_build_dispatch_output_populates_cache_write_tokens() -> None:
    """StepOutput.cache_write_tokens mirrors result.usage cache_write_tokens key."""
    args = _minimal_build_args(
        usage={
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_write_tokens": 55,
        }
    )
    output = build_dispatch_output(*args)
    assert output.cache_write_tokens == 55
    assert output.json is not None
    assert output.json["cache_write_tokens"] == 55
