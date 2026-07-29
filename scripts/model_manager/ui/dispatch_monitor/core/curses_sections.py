"""Section painters extracted from ``CursesBoard`` (SLOC budget)."""

from __future__ import annotations

from typing import Protocol

from .board_lines import attention_line, lease_body_lines
from .dtos import SupervisorProjection


class _BoardSurface(Protocol):
    def _safe_addstr(self, y: int, x: int, text: str, pair: int) -> None: ...

    def _pair_for_severity(self, severity: str) -> int: ...


def paint_lease(
    board: _BoardSurface,
    projection: SupervisorProjection,
    y: int,
    width: int,
    height: int,
) -> int:
    """Paint LEASE / QUEUE body; return next y."""
    if y >= height - 1:
        return y
    health = projection.health
    lease_bar = f" LEASE / QUEUE (q={health.queue_depth}) "
    board._safe_addstr(
        y, 0, f"─{lease_bar}{'─' * max(0, width - len(lease_bar) - 2)}", 4
    )
    y += 1
    for line, pair in lease_body_lines(health):
        if y >= height - 1:
            break
        board._safe_addstr(y, 0, line[: width - 1], pair)
        y += 1
    return y


def paint_attention(
    board: _BoardSurface,
    projection: SupervisorProjection,
    y: int,
    width: int,
    height: int,
    row_cap: int,
) -> None:
    """Paint ATTENTION section in place."""
    if y >= height - 1:
        return
    items = projection.attention
    bar = f" ATTENTION ({len(items)}) "
    board._safe_addstr(y, 0, f"─{bar}{'─' * max(0, width - len(bar) - 2)}", 4)
    y += 1
    if not items and y < height - 1:
        board._safe_addstr(y, 0, "  (none)", 0)
        return
    shown = 0
    for item in items:
        if y >= height - 1 or shown >= row_cap:
            break
        board._safe_addstr(
            y,
            0,
            attention_line(
                item, width - 1, now_ms=projection.generated_at_ms
            ),
            board._pair_for_severity(item.severity),
        )
        y += 1
        shown += 1
    if shown < len(items) and y < height - 1:
        board._safe_addstr(y, 0, f"  … +{len(items) - shown} more", 0)
