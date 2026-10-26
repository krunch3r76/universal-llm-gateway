"""Unit tests for on-behalf sidecar economics helpers."""

from __future__ import annotations

import pytest

from systems.pipeline.core.execution.async_tracker.records import (
    PipelineExecutionRecord,
    PipelineExecutionResult,
)
from systems.pipeline.core.execution.async_tracker_delivery.sidecar import (
    _effective_resolved_model,
    enrich_on_behalf_sidecar_content,
)


def _api_role_record(
    *,
    resolved_model: str | None = None,
    result_model: str = "team-dispatch",
    model_entity_id: str | None = "model:claude-haiku-5-5",
) -> PipelineExecutionRecord:
    return PipelineExecutionRecord(
        execution_id="exec-sidecar-1",
        pipeline="team-dispatch",
        status="completed",
        started_at="2026-10-25T00:00:00Z",
        started_at_monotonic=0.0,
        result=PipelineExecutionResult(
            content="done",
            model=result_model,
            model_entity_id=model_entity_id,
            usage={"input_tokens": 10, "output_tokens": 5},
        ),
        op="to_thread",
        work_key="todo:test",
        endpoint_request_id="req-1",
        resolved_model=resolved_model,
        resolved_contract="freeform",
    )


@pytest.mark.offline
def test_effective_resolved_model_from_entity_id_when_pipeline_slug() -> None:
    record = _api_role_record(resolved_model=None, result_model="team-dispatch")
    assert _effective_resolved_model(record) == "anthropic/claude-haiku-5-5"


@pytest.mark.offline
def test_effective_resolved_model_prefers_admission_resolved_model() -> None:
    record = _api_role_record(
        resolved_model="anthropic/claude-sonnet-4-6",
        result_model="team-dispatch",
    )
    assert _effective_resolved_model(record) == "anthropic/claude-sonnet-4-6"


@pytest.mark.offline
def test_effective_resolved_model_chat_dispatch_slug() -> None:
    record = _api_role_record(
        resolved_model=None,
        result_model="chat-dispatch",
        model_entity_id="model:claude-sonnet-4-6",
    )
    assert _effective_resolved_model(record) == "anthropic/claude-sonnet-4-6"


@pytest.mark.offline
@pytest.mark.offline
def test_enrich_sidecar_includes_archive_uri_and_tool_errors() -> None:
    record = _api_role_record(resolved_model="anthropic/claude-sonnet-4-6")
    record.result.archive_uri = "workspaces://universal-llm-gateway/.runtime/t.json"
    record.result.tool_errors = [
        {"name": "fs", "ok": False, "result": "error"},
    ]
    body = enrich_on_behalf_sidecar_content(record, "closeout")
    assert "archive_uri: workspaces://" in body
    assert "## tool errors" in body
    assert "`fs`" in body


@pytest.mark.offline
def test_enrich_sidecar_uses_entity_id_model_not_pipeline_slug() -> None:
    record = _api_role_record(resolved_model=None, result_model="team-dispatch")
    body = enrich_on_behalf_sidecar_content(record, "closeout")
    assert "## dispatch economics" in body
    # Resolved frontier id is used for pricing, not echoed as a markdown field.
    assert "anthropic/claude-haiku-5-5" not in body
    assert "input_tokens:" in body
