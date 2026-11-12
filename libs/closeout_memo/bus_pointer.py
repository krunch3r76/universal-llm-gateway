"""Compact bus pointers for closeout-memo pipeline overflow posts."""

from __future__ import annotations

from bus_compact_pointer import format_closeout_memo_bus_body

from closeout_memo.models import CloseoutMemoRequest


def _overflow_sidecar_uri(memo: CloseoutMemoRequest) -> str:
    return f"agent-bus:{memo.wake_lane}#closeout-overflow"


def compact_overflow_bus_body(
    *,
    memo: CloseoutMemoRequest,
    overflow_markdown: str,
    sidecar_markdown: str | None = None,
) -> str:
    """Format overflow / fallback bus text as a compact pointer.

    When ``sidecar_markdown`` is supplied with ``memo.sidecar``, the pointer cites
    that URI and sha256 digests those bytes. Otherwise the digest covers
    ``overflow_markdown`` and ``sidecar_ref`` cites the lane overflow spill label
    (never ``memo.sidecar`` with a mismatched hash).
    """
    if memo.sidecar and sidecar_markdown is not None:
        stored_text = sidecar_markdown
        sidecar_uri = memo.sidecar
    else:
        stored_text = overflow_markdown
        sidecar_uri = _overflow_sidecar_uri(memo)
    return format_closeout_memo_bus_body(
        memo_kind=memo.kind,
        memo_status=memo.status,
        wake_lane=memo.wake_lane,
        dispatch_id=memo.dispatch_id,
        execution_id=memo.execution_id,
        sidecar_uri=sidecar_uri,
        sidecar_text=stored_text,
    )
