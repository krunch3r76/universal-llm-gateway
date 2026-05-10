"""Tests for virtual pipeline chat-completion lifecycle helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from systems.pipeline.core.dag import PipelineExecutionError
from systems.pipeline.core.execution.errors import FrontierDispatchExhaustedError
from systems.proxy.stargate.requests.pipeline_lifecycle import (
    _build_recoverable_failure_response,
    _is_recoverable_frontier_exhaustion,
    _pipeline_error_mode,
    _pipeline_execution_error_detail,
)


@dataclass
class _FakeContext:
    selected_model: str = "orion-agent-high"
    original_request: dict[str, Any] | None = None
    http_request: Any = None

    def __post_init__(self) -> None:
        if self.original_request is None:
            self.original_request = {"stream": True}


def _wrapped_frontier_exhaustion() -> PipelineExecutionError:
    cause = FrontierDispatchExhaustedError(
        execution_id="exec-123",
        agent="orion",
        model="openai/gpt-5.4",
        provider="openai",
        turns_used=4,
        tool_calls_made=10,
        finish_reason="tool_calls",
        block_reason=None,
        exhaustion_summary={
            "execution_id": "exec-123",
            "turns_used": 4,
            "tool_calls_made": 10,
            "exhaustion_reason": "repeated_section_not_found",
            "failed_tools": [
                {
                    "tool": "fs.md_read",
                    "code": "section_not_found",
                    "target": "docs/foo.md#Missing",
                    "count": 2,
                    "suggested_next_action": "Run md_list first.",
                }
            ],
            "suggested_continuation": ["Run md_list first."],
        },
    )
    try:
        raise cause
    except FrontierDispatchExhaustedError as exc:
        raise PipelineExecutionError("Step 'respond' failed") from exc


def test_wrapped_frontier_exhaustion_preserves_recoverable_code() -> None:
    try:
        _wrapped_frontier_exhaustion()
    except PipelineExecutionError as exc:
        detail = _pipeline_execution_error_detail(exc, context=_FakeContext())

    assert detail["code"] == "frontier_dispatch_exhausted"
    assert detail["recoverable"] is True
    assert detail["execution_id"] == "exec-123"
    assert detail["exhaustion_summary"]["failed_tools"][0]["code"] == (
        "section_not_found"
    )


def test_streaming_pipeline_error_defaults_to_assistant_message_response() -> None:
    context = _FakeContext()
    assert _pipeline_error_mode(context) == "assistant_message"

    try:
        _wrapped_frontier_exhaustion()
    except PipelineExecutionError as exc:
        detail = _pipeline_execution_error_detail(exc, context=context)

    assert _is_recoverable_frontier_exhaustion(detail) is True
    response = _build_recoverable_failure_response(
        context,
        detail,
        headers={"X-Pipeline-Execution-Id": "exec-123"},
    )
    body = json.loads(response.body)

    assert response.status_code == 200
    assert body["outcome"] == "recoverable_failure"
    assert body["failure"]["code"] == "frontier_dispatch_exhausted"
    assert (
        "I hit the frontier tool-loop budget"
        in body["choices"][0]["message"]["content"]
    )
