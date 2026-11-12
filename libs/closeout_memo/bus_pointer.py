"""Compact bus pointers for closeout-memo pipeline overflow posts."""

from __future__ import annotations

from bus_compact_pointer import format_closeout_memo_bus_body

from closeout_memo.models import CloseoutMemoRequest


def compact_overflow_bus_body(
    *,
    memo: CloseoutMemoRequest | None,
    overflow_markdown: str,
    sidecar_markdown: str | None = None,
    coalesced_count: int = 1,
    spill_uri: str | None = None,
) -> str:
    """Format overflow / fallback bus text as a compact pointer or inline body.

    When ``coalesced_count`` > 1, digest covers ``overflow_markdown`` and
    ``sidecar_ref`` cites a durable cortex spill (never a synthetic bus label).

    With a single memo, ``sidecar_markdown`` plus ``memo.sidecar`` binds sha to
    cortex bytes; otherwise ``spill_uri`` must cite bytes that exist at post time.
    """
    use_cortex = (
        coalesced_count == 1
        and memo is not None
        and memo.sidecar is not None
        and sidecar_markdown is not None
    )
    if use_cortex:
        stored_text = sidecar_markdown
        sidecar_uri: str | None = memo.sidecar
    elif spill_uri:
        stored_text = overflow_markdown
        sidecar_uri = spill_uri
    else:
        stored_text = overflow_markdown
        sidecar_uri = None
    if memo is None:
        return format_closeout_memo_bus_body(
            memo_kind="sdk_closeout",
            memo_status="failed",
            wake_lane="",
            dispatch_id="",
            execution_id=None,
            sidecar_uri=sidecar_uri,
            sidecar_text=stored_text,
        )
    return format_closeout_memo_bus_body(
        memo_kind=memo.kind,
        memo_status=memo.status,
        wake_lane=memo.wake_lane,
        dispatch_id=memo.dispatch_id,
        execution_id=memo.execution_id,
        sidecar_uri=sidecar_uri,
        sidecar_text=stored_text,
    )
