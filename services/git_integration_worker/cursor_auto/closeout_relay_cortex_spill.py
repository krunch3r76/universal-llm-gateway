"""Spill clamped CLOSEOUT relay bodies to cortex; rewrite inline Full closeout pointer."""

from __future__ import annotations

from services.git_integration_worker.cursor_auto.closeout_relay_briefing import (
    clamp_relay_body,
)
from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    CloseoutRelayPayload,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    PinnedWriteFn,
    cortex_promote_ack,
    post_closeout_sidecar,
)


async def promote_clamped_closeout_to_cortex(
    payload: CloseoutRelayPayload,
    *,
    dispatch_id: str,
    thread_id: str,
    post_closeout_sidecar_fn: PinnedWriteFn | None = None,
) -> CloseoutRelayPayload:
    """Write unclamped relay body to cortex and prefer cortex:// in the inline pointer."""
    if not payload.clamped or not payload.body_full:
        return payload

    result = await post_closeout_sidecar(
        full_body=payload.body_full,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        post_pinned=post_closeout_sidecar_fn,
    )
    cortex_uri = cortex_promote_ack(result)
    if not cortex_uri:
        return payload

    clamped_body, was_clamped = clamp_relay_body(payload.body_full, pointer=cortex_uri)
    return CloseoutRelayPayload(
        body=clamped_body,
        status=payload.status,
        source=payload.source,
        body_full=payload.body_full,
        clamped=was_clamped,
    )


__all__ = ["promote_clamped_closeout_to_cortex"]
