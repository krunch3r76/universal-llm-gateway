"""Extract and persist per-dispatch token usage from cursor-sdk runs.

Post-wait ``run.usage`` / ``result.usage`` is authoritative on local runs
(probe 6655 A1 OBSERVED). ``Agent.get_usage`` is cloud-only — local raises
``BadRequestError(code='invalid_argument')``, not ``ConfigurationError``
(OBSERVED probe 6655; SDK docstring stale).

Consumer-facing emit: ``frontier.sdk.worker.completed`` payload fields
``usage`` + ``usage_capture_status``. Closeout assembly carries the same
fields on ``ImplementCloseout`` when post-wait usage is captured.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from services.git_integration_worker.cursor_sdk_usage_normalize import (
    TOTAL_DERIVED_KEY,
    UsageCaptureStatus,
    finalize_usage_with_post_wait,
)


@dataclass(frozen=True)
class DispatchUsageRecord:
    """Normalized usage ready for event emit and ledger persistence."""

    usage: dict[str, Any] | None
    usage_capture_status: UsageCaptureStatus
    reasoning_tokens_note: str | None = None


def extract_post_wait_usage(
    *,
    run: Any = None,
    result: Any = None,
) -> DispatchUsageRecord:
    """Authoritative usage from post-wait handles only (no stream aggregation)."""
    usage, status = finalize_usage_with_post_wait(
        stream_usage=None,
        stream_status="missing",
        run=run,
        result=result,
    )
    return DispatchUsageRecord(
        usage=usage,
        usage_capture_status=status,
        reasoning_tokens_note=_reasoning_tokens_note(usage),
    )


def finalize_dispatch_usage(
    capture: Any,
    *,
    run: Any = None,
    result: Any = None,
) -> DispatchUsageRecord:
    """Merge stream-side usage with post-wait authority."""
    stream_usage = dict(capture.usage) if capture.usage is not None else None
    if stream_usage is not None and getattr(capture, "usage_total_derived", False):
        stream_usage[TOTAL_DERIVED_KEY] = True
    usage, status = finalize_usage_with_post_wait(
        stream_usage=stream_usage,
        stream_status=capture.usage_capture_status,
        run=run,
        result=result,
    )
    return DispatchUsageRecord(
        usage=usage,
        usage_capture_status=status,
        reasoning_tokens_note=_reasoning_tokens_note(usage),
    )


def usage_event_fields(record: DispatchUsageRecord) -> dict[str, Any]:
    """Payload fragment for ``emit_sdk_worker_completed`` / economics consumers."""
    return {
        "usage": record.usage,
        "usage_capture_status": record.usage_capture_status,
    }


def persist_dispatch_usage(
    ledger: Any,
    *,
    dispatch_id: str,
    record: DispatchUsageRecord,
) -> None:
    """Merge usage into ``cursor_sdk_dispatches.record_json`` (durable per dispatch)."""
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch={
            "usage": record.usage,
            "usage_capture_status": record.usage_capture_status,
            "reasoning_tokens_note": record.reasoning_tokens_note,
        },
    )


def read_persisted_usage(record_json: str) -> DispatchUsageRecord | None:
    """Rehydrate a persisted usage record from ledger ``record_json``."""
    try:
        data = json.loads(record_json) if record_json else {}
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "usage_capture_status" not in data:
        return None
    usage = data.get("usage")
    if usage is not None and not isinstance(usage, dict):
        return None
    status = str(data.get("usage_capture_status") or "missing")
    if status not in ("captured", "partial", "missing", "reconciled_delta"):
        status = "missing"
    return DispatchUsageRecord(
        usage=usage,
        usage_capture_status=status,  # type: ignore[arg-type]
        reasoning_tokens_note=data.get("reasoning_tokens_note"),
    )


def _reasoning_tokens_note(usage: dict[str, Any] | None) -> str | None:
    if usage is None:
        return None
    if "reasoning_tokens" not in usage:
        return (
            "reasoning_tokens absent on wire — field not populated for this run shape"
        )
    value = usage.get("reasoning_tokens")
    if value is None:
        return (
            "reasoning_tokens=null on OBSERVED local composer run (6655); "
            "not established whether any local model populates it — carry when present"
        )
    return f"reasoning_tokens={value}"
