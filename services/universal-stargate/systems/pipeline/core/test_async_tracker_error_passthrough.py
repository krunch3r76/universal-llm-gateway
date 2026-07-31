"""Unit tests for async-tracker error data passthrough.

Covers the three paths that led to this phase:
- ProxyClientError with dict detail → data surfaces on tracker record
- ProxyClientError with string detail → data is None (no flattening)
- Generic exception → code/message fall back, data is None
"""

from __future__ import annotations

from typing import Any

import pytest

from systems.pipeline.core.execution.async_tracker import (
    PipelineExecutionTracker,
)
from systems.pipeline.core.execution.proxy_client import ProxyClientError
from systems.pipeline.core.executor import _normalize_pipeline_exception


def _openai_400_body() -> dict[str, Any]:
    return {
        "error": {
            "type": "invalid_request_error",
            "code": "unsupported_parameter",
            "param": "temperature",
            "message": "Unsupported value: 'temperature' does not support 0.7",
        }
    }


def test_normalize_preserves_proxy_error_dict_detail() -> None:
    exc = ProxyClientError(
        "Stargate returned 400: Remote provider rejected request",
        status_code=400,
        detail=_openai_400_body(),
    )
    code, message, data = _normalize_pipeline_exception(exc)
    assert code == "pipeline_execution_failed"
    assert "Stargate returned 400" in message
    assert data == _openai_400_body()


def test_normalize_ignores_proxy_error_string_detail() -> None:
    exc = ProxyClientError(
        "Stargate returned 502: Bad gateway",
        status_code=502,
        detail="<html>…</html>",
    )
    code, _message, data = _normalize_pipeline_exception(exc)
    assert code == "pipeline_execution_failed"
    assert data is None


def test_normalize_returns_none_data_for_plain_exception() -> None:
    exc = RuntimeError("boom")
    code, message, data = _normalize_pipeline_exception(exc)
    assert code == "pipeline_execution_failed"
    assert message == "boom"
    assert data is None


@pytest.mark.asyncio
async def test_fail_execution_records_and_serializes_data() -> None:
    tracker = PipelineExecutionTracker()
    tracker.register_execution(
        execution_id="exec-a",
        pipeline="frontier-dispatch",
        started_at="2026-04-19T00:00:00Z",
    )
    tracker.fail_execution(
        "exec-a",
        code="pipeline_execution_failed",
        message="Upstream 400",
        data=_openai_400_body(),
    )
    record = tracker.get("exec-a")
    assert record is not None
    assert record.status == "failed"
    assert record.error is not None
    assert record.error.data == _openai_400_body()

    payload = record.to_dict()
    assert payload["error"]["data"] == _openai_400_body()
    assert payload["error"]["code"] == "pipeline_execution_failed"


@pytest.mark.asyncio
async def test_fail_execution_data_defaults_none() -> None:
    tracker = PipelineExecutionTracker()
    tracker.register_execution(
        execution_id="exec-b",
        pipeline="frontier-dispatch",
        started_at="2026-04-19T00:00:00Z",
    )
    tracker.fail_execution("exec-b", code="x", message="y")
    record = tracker.get("exec-b")
    assert record is not None
    assert record.error is not None
    assert record.error.data is None
    assert record.to_dict()["error"]["data"] is None
