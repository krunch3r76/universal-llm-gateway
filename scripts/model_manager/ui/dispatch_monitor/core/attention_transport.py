"""Transport-layer attention — GX1 replay truncation."""

from __future__ import annotations

from collections.abc import Mapping

from .dtos import AttentionItem


def transport_truncation_items(
    truncations: Mapping[str, tuple[int | None, str, int | None]],
) -> list[AttentionItem]:
    """Surface per-connection replay truncation to the operator."""
    items: list[AttentionItem] = []
    for connection in sorted(truncations):
        requested, reason, first_seq = truncations[connection]
        detail = f"reason={reason}"
        if requested is not None:
            detail += f", requested_seq={requested}"
        if first_seq is not None:
            detail += f", first_seq={first_seq}"
        items.append(
            AttentionItem(
                key=f"monitor.transport.replay_truncated:{connection}",
                kind="monitor.transport.replay_truncated",
                severity="crit",
                subject=connection,
                title="Replay window truncated — fold may be incomplete",
                detail=detail,
            )
        )
    return items
