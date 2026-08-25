"""Plane, reconcile, and duplicate-refused attention items."""

from __future__ import annotations

from collections.abc import Mapping

from .dtos import AttentionItem, HealthProjection


def plane_items(
    health: HealthProjection,
    *,
    ingest_since_ms: int | None = None,
    subscribe_since_ms: int | None = None,
    unhandled_since_ms: int | None = None,
) -> list[AttentionItem]:
    """Derive attention for realtime-plane drops and schema drift."""
    items: list[AttentionItem] = []
    for count, label, key, since_ms in (
        (
            health.events_dropped_ingest,
            "ingest",
            "events.dropped.ingest",
            ingest_since_ms,
        ),
        (
            health.events_dropped_subscribe,
            "subscribe",
            "events.dropped.subscribe",
            subscribe_since_ms,
        ),
    ):
        if count:
            items.append(
                AttentionItem(
                    key=key,
                    kind=key,
                    severity="warn" if label == "subscribe" else "crit",
                    subject="event-plane",
                    title=f"{count} {label} drop event(s) observed",
                    detail=(
                        "Subscribe drops are correct under overload; ingest drops mean "
                        "fold inputs were lost and folded state may be incomplete."
                    ),
                    since_ms=since_ms,
                )
            )
    if health.unhandled_signals:
        names = ", ".join(sorted(health.unhandled_signals))
        items.append(
            AttentionItem(
                key="signal.unhandled",
                kind="signal.unhandled",
                severity="warn",
                subject="monitor-core",
                title="Signals arrived that the handler table does not cover",
                detail=(
                    f"Unhandled: {names}. Either the emitters moved or this core's "
                    "signal registry is stale. Reconcile signals.py."
                ),
                since_ms=unhandled_since_ms,
            )
        )
    return items


def reconcile_failure_items(
    failures: Mapping[str, tuple[str, str, str] | tuple[str, str, str, int]],
) -> list[AttentionItem]:
    """Surface click-time reconcile source failures to the operator."""
    items: list[AttentionItem] = []
    for key, packed in sorted(failures.items()):
        subject, source, error = packed[0], packed[1], packed[2]
        since_ms = packed[3] if len(packed) > 3 else None
        items.append(
            AttentionItem(
                key=key,
                kind="monitor.reconcile.source_failed",
                severity="warn",
                subject=subject,
                title=f"Reconcile {source} failed",
                detail=f"{source}: {error}",
                since_ms=since_ms,
            )
        )
    return items


def duplicate_refused_items(
    refused: Mapping[str, tuple[int, str, str]],
) -> list[AttentionItem]:
    """Surface duplicate-admit refusals without minting a live dispatch row."""
    items: list[AttentionItem] = []
    for dispatch_id, (since_ms, holder, thread_id) in sorted(refused.items()):
        detail = f"holder={holder}"
        if thread_id:
            detail = f"{detail} thread={thread_id}"
        items.append(
            AttentionItem(
                key=f"sdk.admit.duplicate_refused:{dispatch_id}",
                kind="sdk.admit.duplicate_refused",
                severity="warn",
                subject=dispatch_id,
                title="Admit refused as duplicate of an active peer",
                detail=detail,
                since_ms=since_ms,
            )
        )
    return items
