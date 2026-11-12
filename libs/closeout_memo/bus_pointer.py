"""Compact bus pointers for closeout-memo pipeline overflow posts."""

from __future__ import annotations

from bus_compact_pointer import format_closeout_memo_bus_body

from closeout_memo.models import CloseoutMemoRequest


def compact_overflow_bus_body(
    *,
    memo: CloseoutMemoRequest,
    overflow_markdown: str,
) -> str:
    """Format overflow / fallback bus text as a compact pointer."""
    return format_closeout_memo_bus_body(
        memo_kind=memo.kind,
        memo_status=memo.status,
        wake_lane=memo.wake_lane,
        dispatch_id=memo.dispatch_id,
        execution_id=memo.execution_id,
        sidecar_uri=memo.sidecar,
        sidecar_text=overflow_markdown,
    )
