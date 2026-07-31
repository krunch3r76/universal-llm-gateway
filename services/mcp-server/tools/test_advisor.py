"""Unit tests for advisor transport via chat-dispatch pipeline."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from tools.advisor import _ADVISOR_PIPELINE_TIMEOUT, register_advisor_tools


class _ToolRecorder:
    def __init__(self) -> None:
        self.functions: dict[str, Any] = {}

    def tool(self, **_kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            self.functions[fn.__name__] = fn
            return fn

        return decorator


@pytest.fixture
def advisor_fn() -> Any:
    recorder = _ToolRecorder()
    register_advisor_tools(recorder)  # type: ignore[arg-type]
    return recorder.functions["advisor"]


def test_advisor_success_maps_pipeline_content_to_advice(advisor_fn: Any) -> None:
    pipeline_result = {
        "content": "1. Proceed with the minimal diff.",
        "model": "anthropic/claude-opus-4-6",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    with patch("tools.advisor._pipeline_run", return_value=pipeline_result) as run:
        result = advisor_fn(problem="Should I refactor first?")

    assert result == {
        "advice": "1. Proceed with the minimal diff.",
        "model": "anthropic/claude-opus-4-6",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    run.assert_called_once()
    args, _kwargs = run.call_args
    assert args[0] == "chat-dispatch"
    assert args[3] == _ADVISOR_PIPELINE_TIMEOUT
    assert args[3] == 120.0
    options = args[2]
    assert options["model"] == "anthropic/claude-opus-4-6"
    assert options["max_tokens"] == 1024
    messages = args[1]
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "Should I refactor first?" in messages[1]["content"]


def test_advisor_pipeline_failure_returns_error_envelope(advisor_fn: Any) -> None:
    with patch(
        "tools.advisor._pipeline_run",
        return_value={"error": "Pipeline 'chat-dispatch' timed out after 120.0s."},
    ):
        result = advisor_fn(problem="timeout path")

    assert "error" in result
    assert result["error"] == "Advisor timeout — model may be overloaded"
    assert "advice" not in result


def test_advisor_pipeline_error_includes_detail_when_present(advisor_fn: Any) -> None:
    with patch(
        "tools.advisor._pipeline_run",
        return_value={"error": "Pipeline error: 502 Bad Gateway", "detail": "upstream"},
    ):
        result = advisor_fn(problem="upstream failure")

    assert result["error"] == "Advisor error — pipeline failed"
    assert result["detail"] == "upstream"
