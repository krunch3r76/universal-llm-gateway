"""Item 22 — toolcall result body retention on frontier.sdk.worker.toolcall."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.git_integration_worker import cursor_sdk_stream_capture as capture_mod
from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
    _json_bytes,
    observe_run_stream,
)
from services.git_integration_worker.cursor_sdk_tool_result import unwrap_tool_result
from services.git_integration_worker.cursor_sdk_toolcall_retention import (
    EVENT_PAYLOAD_DROP_AUDIT,
    MAX_RESULT_BODY_BYTES,
    RESULT_BODY_ABSENT_NULL,
    RESULT_BODY_ABSENT_OVERSIZED,
    RESULT_BODY_ABSENT_STREAM_TRUNCATED,
    RESULT_BODY_PRESENT,
    RESULT_RETENTION_WINDOW_S,
    prepare_toolcall_result_retention,
    result_body_from_toolcall_payload,
    retention_window_past_policy,
)

pytestmark = pytest.mark.offline

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "item18_attempt9_live_obs_result.json"
_LIVE_ASSERTION_ID = 27489
_NOW_MS = 1_700_000_000_000


def _load_live_obs_result_body() -> dict[str, object]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_ac22a_present_body_retrievable_from_event_payload() -> None:
    live_body = _load_live_obs_result_body()
    retention = prepare_toolcall_result_retention(
        live_body,
        truncated_fields=(),
        result_bytes=_json_bytes(live_body),
        status="completed",
        now_unix_ms=_NOW_MS,
    )
    fields = retention.as_event_fields()
    assert fields["result_body_status"] == RESULT_BODY_PRESENT
    assert fields["result_body"] == live_body
    body, status, note = result_body_from_toolcall_payload(
        {"result_bytes": _json_bytes(live_body), **fields},
        now_unix_ms=_NOW_MS,
    )
    assert status == RESULT_BODY_PRESENT
    assert note is None
    assert body == live_body


def test_ac22a_explicit_absent_when_null() -> None:
    retention = prepare_toolcall_result_retention(
        None,
        truncated_fields=(),
        result_bytes=0,
        status="completed",
        now_unix_ms=_NOW_MS,
    )
    fields = retention.as_event_fields()
    assert fields["result_body_status"] == RESULT_BODY_ABSENT_NULL
    assert "result_body" not in fields
    body, status, _ = result_body_from_toolcall_payload(fields, now_unix_ms=_NOW_MS)
    assert body is None
    assert status == RESULT_BODY_ABSENT_NULL


def test_ac22a_explicit_absent_when_stream_truncated() -> None:
    retention = prepare_toolcall_result_retention(
        {"content": [{"type": "text", "text": "hidden"}]},
        truncated_fields=("result",),
        result_bytes=100,
        status="completed",
        now_unix_ms=_NOW_MS,
    )
    assert retention.result_body_status == RESULT_BODY_ABSENT_STREAM_TRUNCATED


def test_ac22a_explicit_absent_when_oversized() -> None:
    huge = {"blob": "x" * (MAX_RESULT_BODY_BYTES + 1)}
    retention = prepare_toolcall_result_retention(
        huge,
        truncated_fields=(),
        result_bytes=len(json.dumps(huge)),
        status="completed",
        now_unix_ms=_NOW_MS,
    )
    assert retention.result_body_status == RESULT_BODY_ABSENT_OVERSIZED


def test_ac22b_retention_window_stated_and_past_policy() -> None:
    retention = prepare_toolcall_result_retention(
        {"ok": True},
        truncated_fields=(),
        result_bytes=10,
        status="completed",
        now_unix_ms=_NOW_MS,
    )
    fields = retention.as_event_fields()
    assert fields["result_retention_window_s"] == RESULT_RETENTION_WINDOW_S
    assert fields["result_retention_expires_at_unix_ms"] == _NOW_MS + RESULT_RETENTION_WINDOW_S * 1000
    policy = retention_window_past_policy()
    assert "session boundary" in policy
    body, status, note = result_body_from_toolcall_payload(
        fields,
        now_unix_ms=_NOW_MS + RESULT_RETENTION_WINDOW_S * 1000 + 1,
    )
    assert body is None
    assert note is not None


def test_ac22c_verbatim_live_nested_child_cortex_assert_body() -> None:
    """Quote the attempt-9 live obs.result captured from nested-child cortex assert."""
    live_body = _load_live_obs_result_body()
    payload = unwrap_tool_result(live_body)
    assert isinstance(payload, dict)
    assert payload.get("item", {}).get("id") == _LIVE_ASSERTION_ID
    # Verbatim artifact — serialized fixture from live dispatch attempt 9:
    assert live_body == json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_ac22d_event_payload_drop_audit_covers_siblings() -> None:
    signals = {row["signal"] for row in EVENT_PAYLOAD_DROP_AUDIT}
    assert "frontier.sdk.worker.toolcall" in signals
    assert "frontier.sdk.worker.completed" in signals
    assert "frontier.sdk.worker.delivery_failed" in signals
    toolcall_row = next(
        row for row in EVENT_PAYLOAD_DROP_AUDIT if row["signal"] == "frontier.sdk.worker.toolcall"
    )
    assert toolcall_row["verdict"] == "fixed_item_22"


@pytest.fixture
def _capture_emitted(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    emitted: list[object] = []
    monkeypatch.setattr(
        capture_mod, "emit_frontier_event", lambda event: emitted.append(event)
    )
    return emitted


def test_stream_emit_includes_retained_body_for_cortex_assert(
    _capture_emitted: list[object],
) -> None:
    from services.git_integration_worker.tests.test_cursor_sdk_stream_capture import (
        _FakeRun,
        _FakeToolCallMessage,
    )

    live_body = _load_live_obs_result_body()
    run = _FakeRun(
        messages=[
            _FakeToolCallMessage(
                call_id="c-cortex-live",
                name="mcp",
                status="completed",
                args={
                    "providerIdentifier": "user-vortex",
                    "toolName": "cortex",
                    "args": {"tool": "assert", "entity_id": "todo:ac9g-live-falsifier"},
                },
                result=live_body,
            ),
        ]
    )
    observe_run_stream(
        run, dispatch_id="d22", thread_id="6655", resolved_model="cursor/composer-2.5"
    )
    assert len(_capture_emitted) == 1
    payload = _capture_emitted[0].payload
    assert payload["result_body_status"] == RESULT_BODY_PRESENT
    assert payload["result_body"] == live_body
    assert payload["result_bytes"] == _json_bytes(live_body)
    assert payload["result_retention_window_s"] == RESULT_RETENTION_WINDOW_S


def test_legacy_metadata_only_payload_surfaces_explicit_legacy_status() -> None:
    body, status, note = result_body_from_toolcall_payload(
        {"result_bytes": 2713, "tool_name": "cortex"},
        now_unix_ms=_NOW_MS,
    )
    assert body is None
    assert status == "absent_legacy_metadata_only"
    assert note is not None
