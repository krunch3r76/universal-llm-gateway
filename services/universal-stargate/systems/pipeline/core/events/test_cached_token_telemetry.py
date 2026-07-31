"""Unit tests for optional cached_tokens on step/frontier completion events."""

from __future__ import annotations

from systems.pipeline.core.events.dispatch.frontier_lifecycle import (
    PipelineFrontierDispatchCompleted,
)
from systems.pipeline.core.events.step.lifecycle import StepCompleted


def test_step_completed_includes_cached_tokens_when_set() -> None:
    event = StepCompleted(
        pipeline_id="pipe-1",
        execution_id="exec-1",
        step_name="respond",
        duration_seconds=1.2,
        output_length=100,
        prompt_tokens=500,
        completion_tokens=50,
        model_call_count=1,
        cached_tokens=120,
    )
    assert event.signal == "pipeline.step.completed"
    assert event.payload["cached_tokens"] == 120


def test_step_completed_omits_cached_tokens_when_none() -> None:
    event = StepCompleted(
        pipeline_id="pipe-1",
        execution_id="exec-1",
        step_name="respond",
        duration_seconds=1.2,
        output_length=100,
        prompt_tokens=500,
        completion_tokens=50,
        model_call_count=1,
    )
    assert "cached_tokens" not in event.payload


def test_frontier_dispatch_completed_includes_cached_tokens_when_set() -> None:
    event = PipelineFrontierDispatchCompleted(
        agent="artisan",
        execution_id="exec-1",
        turns_used=2,
        tool_calls_made=1,
        reasoning_present=False,
        prompt_tokens=800,
        completion_tokens=120,
        provider="openai",
        cached_tokens=300,
    )
    assert event.signal == "pipeline.frontier.dispatch.completed"
    assert event.payload["cached_tokens"] == 300


def test_frontier_dispatch_completed_omits_cached_tokens_when_none() -> None:
    event = PipelineFrontierDispatchCompleted(
        agent="artisan",
        execution_id="exec-1",
        turns_used=2,
        tool_calls_made=1,
        reasoning_present=False,
        prompt_tokens=800,
        completion_tokens=120,
        provider="xai",
    )
    assert "cached_tokens" not in event.payload
