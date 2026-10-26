"""Outcome assembly archive_uri and tool_errors extraction."""

from __future__ import annotations

import pytest

from systems.pipeline.core.executor.outcome_assembly import (
    _extract_archive_uri_and_tool_errors,
)
from systems.pipeline.core.handlers import StepOutput
from systems.pipeline.core.schemas import StepConfig


class _Ctx:
    def __init__(self, outputs: dict) -> None:
        self.outputs = outputs


@pytest.mark.offline
def test_extract_archive_uri_and_tool_errors_from_dag_outputs() -> None:
    ctx = _Ctx(
        {
            "respond": StepOutput(
                raw="answer",
                prompt_tokens=1,
                completion_tokens=2,
                latency_ms=1.0,
                json={
                    "tool_calls": [
                        {"name": "ok_tool", "ok": True},
                        {"name": "bad_tool", "ok": False, "result": "denied"},
                    ]
                },
            ),
            "archive_assistant": StepOutput(
                raw="",
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0.0,
                json={
                    "artifact_uri": "workspaces://universal-llm-gateway/.runtime/x.json"
                },
            ),
        }
    )
    steps = [
        StepConfig(id="respond", type="frontier_dispatch_v1"),
        StepConfig(id="archive_assistant", type="archive_assistant_turn_v1"),
    ]
    archive_uri, tool_errors = _extract_archive_uri_and_tool_errors(
        ctx,
        steps,
        ["respond", "archive_assistant"],
    )
    assert archive_uri.endswith("x.json")
    assert len(tool_errors) == 1
    assert tool_errors[0]["name"] == "bad_tool"
