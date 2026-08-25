"""SDK-family attention items, including closeout ``target_uri`` stamping."""

from __future__ import annotations

from dataclasses import replace

from .attention_util import escalate, secs
from .dtos import AttentionItem, SdkDispatchRow, Thresholds


def sdk_items(
    dispatches: tuple[SdkDispatchRow, ...], thresholds: Thresholds
) -> list[AttentionItem]:
    """Derive attention for cursor-sdk dispatches, including GS2 divergence."""
    items: list[AttentionItem] = []
    for row in dispatches:
        if row.divergent_fields:
            items.append(
                AttentionItem(
                    key=f"sdk.emitter.divergence:{row.dispatch_id}",
                    kind="sdk.emitter.divergence",
                    severity="crit",
                    subject=row.dispatch_id,
                    title="GS2: emitters disagree about this dispatch",
                    detail=(
                        "Divergent fields: "
                        + ", ".join(row.divergent_fields)
                        + f". Emitters: {', '.join(row.emitters_seen)}; first terminal "
                        f"from {row.terminal_emitter or 'unknown'} was kept. "
                        "Not reconciled by design."
                    ),
                    since_ms=row.terminal_ms,
                )
            )
        if row.state == "failed":
            items.append(
                AttentionItem(
                    key=f"sdk.dispatch.failed:{row.dispatch_id}",
                    kind="sdk.dispatch.failed",
                    severity="crit",
                    subject=row.dispatch_id,
                    title="Dispatch failed",
                    detail=row.failure_reason or "no reason reported",
                    since_ms=row.terminal_ms,
                )
            )
        if row.state == "timeout":
            items.append(
                AttentionItem(
                    key=f"sdk.dispatch.timeout:{row.dispatch_id}",
                    kind="sdk.dispatch.timeout",
                    severity="crit",
                    subject=row.dispatch_id,
                    title="Dispatch timed out",
                    detail=row.failure_reason or "worker exceeded wall budget",
                    since_ms=row.terminal_ms,
                )
            )
        if row.state == "orphaned":
            items.append(
                AttentionItem(
                    key=f"sdk.dispatch.orphaned:{row.dispatch_id}",
                    kind="sdk.dispatch.orphaned",
                    severity="crit",
                    subject=row.dispatch_id,
                    title="Dispatch orphaned",
                    detail=row.failure_reason or "bridge lost while worker running",
                    since_ms=row.terminal_ms,
                )
            )
        if row.state == "cancelled":
            items.append(
                AttentionItem(
                    key=f"sdk.dispatch.cancelled:{row.dispatch_id}",
                    kind="sdk.dispatch.cancelled",
                    severity="warn",
                    subject=row.dispatch_id,
                    title="Dispatch cancelled",
                    detail=row.failure_reason or "worker cancelled (supersede or operator)",
                    since_ms=row.terminal_ms,
                )
            )
        if row.delivery_failed:
            items.append(
                AttentionItem(
                    key=f"sdk.dispatch.delivery-failed:{row.dispatch_id}",
                    kind="sdk.dispatch.delivery_failed",
                    severity="crit",
                    subject=row.dispatch_id,
                    title="Dispatch delivery failed",
                    detail=row.failure_reason or "run ok, bus post failed",
                    since_ms=row.last_progress_ms,
                )
            )
        if row.implement_gate_bypass:
            items.append(
                AttentionItem(
                    key=f"sdk.dispatch.implement_gate_bypass:{row.dispatch_id}",
                    kind="sdk.dispatch.implement_gate_bypass",
                    severity="warn",
                    subject=row.dispatch_id,
                    title="Implement dispatch admitted without source_ref",
                    detail=(
                        "Readiness gate no-opped; deviation "
                        "gate:implement_source_ref_unresolved"
                    ),
                    since_ms=row.last_progress_ms,
                )
            )
        if row.lease_released_without_terminal and row.terminal_ms is None:
            items.append(
                AttentionItem(
                    key=f"sdk.dispatch.lease_released_without_terminal:{row.dispatch_id}",
                    kind="sdk.dispatch.lease_released_without_terminal",
                    severity="crit",
                    subject=row.dispatch_id,
                    title="Lease released without worker terminal",
                    detail=(
                        "Write lease released while row has no foldable worker "
                        "terminal — GIW emit regression loud"
                    ),
                    since_ms=row.last_progress_ms,
                )
            )
        if row.terminal_ms is None:
            severity = escalate(
                row.idle_age_ms,
                thresholds.sdk_idle_warn_ms,
                thresholds.sdk_idle_crit_ms,
            )
            if severity:
                items.append(
                    AttentionItem(
                        key=f"sdk.dispatch.idle:{row.dispatch_id}",
                        kind="sdk.dispatch.idle",
                        severity=severity,
                        subject=row.dispatch_id,
                        title="Dispatch has emitted no progress recently",
                        detail=(
                            f"Idle {secs(row.idle_age_ms)}"
                            + (f" at stage {row.stall_stage}" if row.stall_stage else "")
                            + ". Idle window, not a completion deadline."
                        ),
                        since_ms=row.last_progress_ms,
                        age_ms=row.idle_age_ms,
                    )
                )
    stamped: list[AttentionItem] = []
    closeout = {row.dispatch_id: row.closeout_uri for row in dispatches}
    for item in items:
        uri = closeout.get(item.subject)
        if uri and item.target_uri is None:
            stamped.append(replace(item, target_uri=uri))
        else:
            stamped.append(item)
    return stamped
