"""Unit tests for dispatch-economics-token-rollup."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from event_store.dispatch_economics_core import (
    map_cdp_stub,
    map_pipeline_row,
    map_sdk_row,
    map_snapshot_row,
)
from event_store.dispatch_economics_rollup import (
    build_dispatch_economics_rollup,
)
from event_store.operation_catalog import get_operation, list_operations
from event_store.operation_dispatch import _DISPATCH, execute_operation
from event_store.store import EventStore


def _event_row(
    *,
    seq: int,
    signal: str,
    payload: dict,
    execution_id: str | None = None,
    request_id: str | None = None,
) -> dict:
    return {
        "seq": seq,
        "signal": signal,
        "execution_id": execution_id,
        "request_id": request_id,
        "ts_unix_ms": 1_700_000_000_000 + seq,
        "payload": payload,
    }


def test_map_sdk_preserves_null_not_zero() -> None:
    row = map_sdk_row(
        {"seq": 1},
        {
            "dispatch_id": "d1",
            "execution_id": "e1",
            "usage_capture_status": "captured",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_tokens": None,
            },
        },
    )
    assert row["prompt_tokens"] == 10
    assert row["cache_read_tokens"] is None
    assert row["usage_capture_status"] == "captured"


def test_map_sdk_preserves_model_knobs_requested() -> None:
    row = map_sdk_row(
        {"seq": 1},
        {
            "dispatch_id": "d1",
            "execution_id": "e1",
            "resolved_model": "cursor/grok-4.6",
            "model_knobs_requested": {"fast": "true", "effort": "high"},
            "usage_capture_status": "captured",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    )
    assert row["model_id"] == "cursor/grok-4.6"
    assert row["model_knobs_requested"] == {"fast": "true", "effort": "high"}


def test_map_snapshot_nested_usage_parent_signal() -> None:
    row = map_snapshot_row(
        {"seq": 2, "request_id": "req-1"},
        {
            "request_id": "req-1",
            "model_id": "anthropic/claude-opus-4-8",
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": 15},
            },
        },
    )
    assert row["prompt_tokens"] == 100
    assert row["cache_read_tokens"] == 15
    assert row["cache_write_tokens"] is None
    assert row["usage_capture_status"] == "captured"


def test_map_pipeline_flat_tokens() -> None:
    row = map_pipeline_row(
        {"seq": 3, "execution_id": "exec-1"},
        {
            "execution_id": "exec-1",
            "prompt_tokens": 50,
            "completion_tokens": 25,
            "cached_tokens": 5,
            "model_entity_id": "model:gpt-5.5",
        },
    )
    assert row["prompt_tokens"] == 50
    assert row["cache_read_tokens"] == 5
    assert row["total_tokens"] == 75


def test_dedupe_prefers_sdk_captured_over_pipeline() -> None:
    sdk = _event_row(
        seq=1,
        signal="frontier.sdk.worker.completed",
        execution_id="shared-eid",
        payload={
            "dispatch_id": "d-shared",
            "execution_id": "shared-eid",
            "usage_capture_status": "captured",
            "usage": {"input_tokens": 100, "output_tokens": 40},
        },
    )
    pipeline = _event_row(
        seq=2,
        signal="pipeline.frontier.dispatch.completed",
        execution_id="shared-eid",
        payload={
            "execution_id": "shared-eid",
            "prompt_tokens": 90,
            "completion_tokens": 40,
        },
    )
    body = build_dispatch_economics_rollup(
        sdk_rows=[sdk],
        snapshot_rows=[],
        pipeline_rows=[pipeline],
        cdp_stubs=[],
    )
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["join_quality"] == "coalesced"
    assert row["prompt_tokens"] == 100
    assert row["completion_tokens"] == 40
    assert row["dispatch_id"] == "d-shared"
    assert row["merge_conflict"] is True
    assert body["join_audit"]["merge_conflict_count"] == 1


def test_merge_conflict_when_coalesced_values_disagree() -> None:
    sdk = _event_row(
        seq=1,
        signal="frontier.sdk.worker.completed",
        execution_id="shared-eid",
        payload={
            "execution_id": "shared-eid",
            "usage_capture_status": "captured",
            "usage": {"input_tokens": 100, "output_tokens": 40},
        },
    )
    pipeline = _event_row(
        seq=2,
        signal="pipeline.frontier.dispatch.completed",
        execution_id="shared-eid",
        payload={
            "execution_id": "shared-eid",
            "prompt_tokens": 120,
            "completion_tokens": 40,
        },
    )
    body = build_dispatch_economics_rollup(
        sdk_rows=[sdk],
        snapshot_rows=[],
        pipeline_rows=[pipeline],
        cdp_stubs=[],
    )
    row = body["rows"][0]
    assert row["merge_conflict"] is True
    assert "prompt_tokens" in row["conflict_vectors"]
    assert body["join_audit"]["merge_conflict_count"] == 1


def test_cdp_stub_unavailable_and_excluded_from_orphan_rate() -> None:
    stub = map_cdp_stub(execution_id="cdp-eid", archived_at="2026-07-20T00:00:00+00:00")
    sdk = _event_row(
        seq=1,
        signal="frontier.sdk.worker.completed",
        execution_id="sdk-eid",
        payload={
            "execution_id": "sdk-eid",
            "usage_capture_status": "captured",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    )
    body = build_dispatch_economics_rollup(
        sdk_rows=[sdk],
        snapshot_rows=[],
        pipeline_rows=[],
        cdp_stubs=[stub],
    )
    cdp_rows = [row for row in body["rows"] if row["substrate"] == "web-anthropic-cdp"]
    assert len(cdp_rows) == 1
    assert cdp_rows[0]["usage_capture_status"] == "unavailable"
    assert cdp_rows[0]["prompt_tokens"] is None
    assert body["join_audit"]["cdp_stub_count"] == 1
    assert body["join_audit"]["orphan_rate"] == 0.0


def test_summary_includes_coverage_counts() -> None:
    sdk = _event_row(
        seq=1,
        signal="frontier.sdk.worker.completed",
        execution_id="e1",
        payload={
            "execution_id": "e1",
            "usage_capture_status": "captured",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    )
    stub = map_cdp_stub(execution_id="cdp-eid", archived_at=None)
    body = build_dispatch_economics_rollup(
        sdk_rows=[sdk],
        snapshot_rows=[],
        pipeline_rows=[],
        cdp_stubs=[stub],
    )
    summary = body["summary"]
    assert summary["prompt_tokens"] == 10
    assert summary["prompt_tokens_coverage"]["captured"] == 1
    assert summary["prompt_tokens_coverage"]["unavailable"] == 1
    assert summary["comparable_total_tokens"] == 15


def test_comparable_total_prefers_prompt_plus_completion() -> None:
    sdk = _event_row(
        seq=1,
        signal="frontier.sdk.worker.completed",
        execution_id="e1",
        payload={
            "execution_id": "e1",
            "usage_capture_status": "captured",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 999},
        },
    )
    body = build_dispatch_economics_rollup(
        sdk_rows=[sdk],
        snapshot_rows=[],
        pipeline_rows=[],
        cdp_stubs=[],
    )
    row = body["rows"][0]
    assert row["total_tokens"] == 999
    assert row["comparable_total_tokens"] == 15


@pytest.mark.asyncio
async def test_operation_registered_and_dispatched() -> None:
    names = {op["name"] for op in list_operations()}
    assert "dispatch-economics-token-rollup" in names
    assert get_operation("dispatch-economics-token-rollup") is not None
    assert "dispatch-economics-token-rollup" in _DISPATCH

    store = EventStore(":memory:")
    await store.open()
    try:
        await store.insert_events(
            [
                {
                    "signal": "frontier.sdk.worker.completed",
                    "role": "observation",
                    "scope": "node",
                    "ts_unix_ms": 1_700_000_000_000,
                    "timestamp": "2026-07-20T00:00:00Z",
                    "source": "test",
                    "payload": {
                        "execution_id": "exec-live",
                        "usage_capture_status": "captured",
                        "usage": {"input_tokens": 3, "output_tokens": 2},
                    },
                }
            ]
        )
        body = await execute_operation(
            "dispatch-economics-token-rollup",
            {"since_ts": 0},
            store,
        )
    finally:
        await store.close()

    assert body["rows"]
    assert "join_audit" in body
    assert "double_count_rate" in body["join_audit"]


def test_mcp_events_allowlist_accepts_dispatch_economics_rollup() -> None:
    events_path = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "mcp-server"
        / "tools"
        / "events.py"
    )
    tree = ast.parse(events_path.read_text(encoding="utf-8"))
    valid_operations: set[str] | None = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "_VALID_OPERATIONS":
                value = node.value
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                    if value.func.id == "frozenset" and value.args:
                        valid_operations = set(ast.literal_eval(value.args[0]))
                break
    assert isinstance(valid_operations, set)
    assert "dispatch-economics-token-rollup" in valid_operations


def test_anthropic_exception_inventory_procedure() -> None:
    """AC-9: 30d falsifier — exception paths must use parent request.snapshot.completed.

    Procedure (manual / scheduled):
      observability(operation="signal-events", params={
        "signal": "request.snapshot.completed",
        "minutes": 43200,
        "limit": 500,
      })
    Then filter payload.model_id for anthropic-family models and assert nested
    `usage` is present on the parent signal (not a child signal name).
    """
    snapshot = _event_row(
        seq=99,
        signal="request.snapshot.completed",
        request_id="anthropic-req",
        payload={
            "request_id": "anthropic-req",
            "model_id": "anthropic/claude-opus-4-8",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    )
    body = build_dispatch_economics_rollup(
        sdk_rows=[],
        snapshot_rows=[snapshot],
        pipeline_rows=[],
        cdp_stubs=[],
    )
    row = body["rows"][0]
    assert row["substrate"] == "stargate-snapshot"
    assert row["model_id"].startswith("anthropic/")
    assert row["prompt_tokens"] == 1
