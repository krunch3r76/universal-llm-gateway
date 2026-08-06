"""Spill CLOSEOUT acceptance prose to cortex; rewrite inline Full closeout pointer.

Row 13: durability is independent of bus clamp. Acceptance evidence SoT for life
adjudication is the cortex twin (prose/sidecar text), not the shared-checkout
``workspaces://…/tmp/reviews/closeouts/`` path and not ImplementCloseout JSON
that may first-write the same URI. Promote overwrites (``write_if_absent=False``)
so a prior oversize-JSON relocate cannot own the acceptance URI.
"""

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

_FULL_CLOSEOUT_PREFIX = "\n\nFull closeout: "


async def promote_clamped_closeout_to_cortex(
    payload: CloseoutRelayPayload,
    *,
    dispatch_id: str,
    thread_id: str,
    sidecar_text: str | None = None,
    post_closeout_sidecar_fn: PinnedWriteFn | None = None,
) -> CloseoutRelayPayload:
    """Write acceptance closeout prose to cortex and prefer cortex:// in the pointer.

    Spill fires whenever prose exists (``sidecar_text`` or ``body_full``), not only
    when the bus body was clamped. Side effect: pinned cortex write with
    ``write_if_absent=False`` so acceptance prose displaces a JSON-only twin.
    """
    raw_closeout = sidecar_text if sidecar_text is not None else payload.body_full
    if not raw_closeout:
        return payload

    result = await post_closeout_sidecar(
        full_body=raw_closeout,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        post_pinned=post_closeout_sidecar_fn,
        write_if_absent=False,
    )
    cortex_uri = cortex_promote_ack(result)
    if not cortex_uri:
        return payload

    if payload.clamped and payload.body_full:
        clamped_body, was_clamped = clamp_relay_body(
            payload.body_full, pointer=cortex_uri
        )
        return CloseoutRelayPayload(
            body=clamped_body,
            status=payload.status,
            source=payload.source,
            body_full=payload.body_full,
            clamped=was_clamped,
            relay_note=payload.relay_note,
            deployment_state=payload.deployment_state,
        )

    body = payload.body
    if "Full closeout:" not in body:
        body = f"{body}{_FULL_CLOSEOUT_PREFIX}{cortex_uri}"
    return CloseoutRelayPayload(
        body=body,
        status=payload.status,
        source=payload.source,
        body_full=payload.body_full,
        clamped=payload.clamped,
        relay_note=payload.relay_note,
        deployment_state=payload.deployment_state,
    )


__all__ = ["promote_clamped_closeout_to_cortex"]
