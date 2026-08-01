"""Tests for cursor_sdk_usage_extract — post-wait authority + ledger persistence."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    _connect,
)
from services.git_integration_worker.cursor_sdk_stream_capture import StreamCapture
from services.git_integration_worker.cursor_sdk_usage_extract import (
    extract_post_wait_usage,
    finalize_dispatch_usage,
    persist_dispatch_usage,
    read_persisted_usage,
    usage_event_fields,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)
from services.git_integration_worker.tests.test_write_lease_refusal import (
    _admit,
    _req,
)


@dataclass
class _FakeUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int | None = None


@dataclass
class _FakeResult:
    usage: _FakeUsage
    status: str = "completed"


class _FakeRun:
    def __init__(self, usage: _FakeUsage | None = None) -> None:
        self.usage = usage


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def test_extract_post_wait_usage_captured() -> None:
    usage = _FakeUsage(100, 50, 150)
    record = extract_post_wait_usage(run=_FakeRun(usage), result=_FakeResult(usage))
    assert record.usage is not None
    assert record.usage["input_tokens"] == 100
    assert record.usage["output_tokens"] == 50
    assert record.usage["total_tokens"] == 150
    assert record.usage_capture_status == "captured"
    fields = usage_event_fields(record)
    assert fields["usage"]["total_tokens"] == 150
    assert fields["usage_capture_status"] == "captured"


def test_finalize_dispatch_usage_reconciles_stream() -> None:
    capture = StreamCapture(
        tool_calls=(),
        usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        usage_capture_status="partial",
    )
    record = finalize_dispatch_usage(
        capture,
        run=_FakeRun(_FakeUsage(100, 50, 150)),
        result=_FakeResult(_FakeUsage(100, 50, 150)),
    )
    assert record.usage_capture_status == "reconciled_delta"
    assert record.usage is not None
    assert record.usage["total_tokens"] == 150


def test_persist_dispatch_usage_roundtrip() -> None:
    ledger = CursorDispatchLedger.instance()
    dispatch_id = "usage-persist-test"
    _admit(ledger, _req(dispatch_id=dispatch_id))
    record = extract_post_wait_usage(
        run=_FakeRun(),
        result=_FakeResult(_FakeUsage(36080, 299, 61435, cache_read_tokens=25056)),
    )
    persist_dispatch_usage(ledger, dispatch_id=dispatch_id, record=record)
    with _connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    assert row is not None
    restored = read_persisted_usage(row["record_json"])
    assert restored is not None
    assert restored.usage is not None
    assert restored.usage["input_tokens"] == 36080
    assert restored.usage["total_tokens"] == 61435
    assert restored.usage_capture_status == "captured"
