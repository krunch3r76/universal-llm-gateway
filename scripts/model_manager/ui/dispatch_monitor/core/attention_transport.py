"""Transport-layer attention — GX1 replay truncation."""

from __future__ import annotations

from collections.abc import Mapping

from .dtos import AttentionItem


def transport_truncation_items(
    truncations: Mapping[str, tuple],
    *,
    now_ms: int | None = None,
) -> list[AttentionItem]:
    """Surface per-connection replay truncation to the operator."""
    items: list[AttentionItem] = []
    for connection in sorted(truncations):
        packed = truncations[connection]
        requested, reason, first_seq = packed[0], packed[1], packed[2]
        since_ms = packed[3] if len(packed) > 3 else None
        detail = f"reason={reason}"
        if requested is not None:
            detail += f", requested_seq={requested}"
        if first_seq is not None:
            detail += f", first_seq={first_seq}"
        age_ms = None
        if since_ms is not None and now_ms is not None:
            age_ms = max(0, now_ms - since_ms)
        items.append(
            AttentionItem(
                key=f"monitor.transport.replay_truncated:{connection}",
                kind="monitor.transport.replay_truncated",
                severity="crit",
                subject=connection,
                title="Replay window truncated — fold may be incomplete",
                detail=detail,
                since_ms=since_ms,
                age_ms=age_ms,
            )
        )
    return items
