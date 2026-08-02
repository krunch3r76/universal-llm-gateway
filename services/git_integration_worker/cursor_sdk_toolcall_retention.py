"""Retain toolcall result bodies on ``frontier.sdk.worker.toolcall`` events (item 22).

Silent metadata-only toolcall rows (``result_bytes`` without a retrievable body)
blocked item-18 harvest. This module makes presence or explicit absence part of
the event contract and exposes a small retrieval helper for downstream callers.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# Observation-tier events inherit the event-store session prune (~2 ``system.started``
# boundaries). The window is stated on every emitted row so callers know when the
# body stops being queryable from SQLite / observability.
RESULT_RETENTION_WINDOW_S = 7 * 24 * 3600

# Event store rejects payloads above 64KiB; reserve headroom for sibling fields.
MAX_RESULT_BODY_BYTES = 32_768

RESULT_BODY_PRESENT = "present"
RESULT_BODY_ABSENT_NULL = "absent_null"
RESULT_BODY_ABSENT_STREAM_TRUNCATED = "absent_stream_truncated"
RESULT_BODY_ABSENT_OVERSIZED = "absent_oversized"
RESULT_BODY_ABSENT_ERROR = "absent_error"

_PAST_RETENTION = (
    "Body no longer retained: observation-tier events are pruned at the event-store "
    "session boundary (older than the two most recent ``system.started`` markers). "
    "Query ``result_body_status`` on surviving rows; absent fields mean expired or "
    "never captured."
)


@dataclass(frozen=True)
class ToolcallResultRetention:
    result_body: object | None
    result_body_status: str
    result_retention_window_s: int
    result_retention_expires_at_unix_ms: int

    def as_event_fields(self) -> dict[str, object]:
        fields: dict[str, object] = {
            "result_body_status": self.result_body_status,
            "result_retention_window_s": self.result_retention_window_s,
            "result_retention_expires_at_unix_ms": self.result_retention_expires_at_unix_ms,
        }
        if self.result_body is not None:
            fields["result_body"] = self.result_body
        return fields


def _serialize_result(result: object) -> tuple[object | None, int | None]:
    if isinstance(result, (dict, list, str, int, float, bool)) or result is None:
        try:
            encoded = json.dumps(result, default=str, separators=(",", ":")).encode("utf-8")
            return result, len(encoded)
        except (TypeError, ValueError):
            return {"raw": str(result)}, len(str(result).encode("utf-8"))
    text = str(result)
    return {"raw": text}, len(text.encode("utf-8"))


def prepare_toolcall_result_retention(
    result: object | None,
    *,
    truncated_fields: tuple[str, ...],
    result_bytes: int,
    status: str,
    now_unix_ms: int | None = None,
) -> ToolcallResultRetention:
    """Build explicit retention fields for one terminal toolcall emission."""
    now_ms = int(time.time() * 1000) if now_unix_ms is None else now_unix_ms
    expires_ms = now_ms + RESULT_RETENTION_WINDOW_S * 1000

    if str(status).lower() == "error":
        return ToolcallResultRetention(
            result_body=None,
            result_body_status=RESULT_BODY_ABSENT_ERROR,
            result_retention_window_s=RESULT_RETENTION_WINDOW_S,
            result_retention_expires_at_unix_ms=expires_ms,
        )

    if "result" in truncated_fields:
        return ToolcallResultRetention(
            result_body=None,
            result_body_status=RESULT_BODY_ABSENT_STREAM_TRUNCATED,
            result_retention_window_s=RESULT_RETENTION_WINDOW_S,
            result_retention_expires_at_unix_ms=expires_ms,
        )

    if result is None:
        return ToolcallResultRetention(
            result_body=None,
            result_body_status=RESULT_BODY_ABSENT_NULL,
            result_retention_window_s=RESULT_RETENTION_WINDOW_S,
            result_retention_expires_at_unix_ms=expires_ms,
        )

    serializable, encoded_len = _serialize_result(result)
    if encoded_len is not None and encoded_len > MAX_RESULT_BODY_BYTES:
        return ToolcallResultRetention(
            result_body=None,
            result_body_status=RESULT_BODY_ABSENT_OVERSIZED,
            result_retention_window_s=RESULT_RETENTION_WINDOW_S,
            result_retention_expires_at_unix_ms=expires_ms,
        )

    # ``result_bytes`` is the stream fingerprint; retention stores the raw shape.
    _ = result_bytes
    return ToolcallResultRetention(
        result_body=serializable,
        result_body_status=RESULT_BODY_PRESENT,
        result_retention_window_s=RESULT_RETENTION_WINDOW_S,
        result_retention_expires_at_unix_ms=expires_ms,
    )


def result_body_from_toolcall_payload(
    payload: Mapping[str, Any],
    *,
    now_unix_ms: int | None = None,
) -> tuple[object | None, str, str | None]:
    """Return ``(body, status, past_retention_note)`` for a toolcall event payload."""
    status = str(payload.get("result_body_status") or "")
    if not status:
        if payload.get("result_bytes") and not payload.get("result_body"):
            return None, "absent_legacy_metadata_only", _PAST_RETENTION
        return None, "absent_unmarked", _PAST_RETENTION

    expires_ms = payload.get("result_retention_expires_at_unix_ms")
    now_ms = int(time.time() * 1000) if now_unix_ms is None else now_unix_ms
    if isinstance(expires_ms, int) and now_ms > expires_ms:
        return None, status, _PAST_RETENTION

    if status == RESULT_BODY_PRESENT:
        return payload.get("result_body"), status, None
    return None, status, None


def retention_window_past_policy() -> str:
    """Human-readable statement of what happens after the retention window."""
    return _PAST_RETENTION


# AC-22d — sibling factories audited for metadata-without-payload (item 21 clause).
EVENT_PAYLOAD_DROP_AUDIT: tuple[dict[str, str], ...] = (
    {
        "signal": "frontier.sdk.worker.toolcall",
        "fields_without_body": "result_bytes, truncated",
        "verdict": "fixed_item_22",
        "notes": "Now carries result_body or result_body_status.",
    },
    {
        "signal": "frontier.sdk.worker.completed",
        "fields_without_body": "result_bytes, tool_call_count",
        "verdict": "intentional_aggregate",
        "notes": "Terminal summary; full Composer output lives in repo sidecar.",
    },
    {
        "signal": "frontier.sdk.worker.delivery_failed",
        "fields_without_body": "result_bytes",
        "verdict": "intentional_pointer",
        "notes": "Carries sidecar_ref pointer to persisted closeout body.",
    },
    {
        "signal": "frontier.sdk.worker.progress",
        "fields_without_body": "tool_call_count, elapsed_s",
        "verdict": "intentional_aggregate",
        "notes": "Heartbeat counter only; per-call detail is on toolcall rows.",
    },
    {
        "signal": "frontier.sdk.worker.failed",
        "fields_without_body": "error, detail_summary",
        "verdict": "partial_text_retained",
        "notes": "Error string retained; no separate result_bytes field.",
    },
)


__all__ = [
    "EVENT_PAYLOAD_DROP_AUDIT",
    "MAX_RESULT_BODY_BYTES",
    "RESULT_BODY_ABSENT_ERROR",
    "RESULT_BODY_ABSENT_NULL",
    "RESULT_BODY_ABSENT_OVERSIZED",
    "RESULT_BODY_ABSENT_STREAM_TRUNCATED",
    "RESULT_BODY_PRESENT",
    "RESULT_RETENTION_WINDOW_S",
    "ToolcallResultRetention",
    "prepare_toolcall_result_retention",
    "result_body_from_toolcall_payload",
    "retention_window_past_policy",
]
