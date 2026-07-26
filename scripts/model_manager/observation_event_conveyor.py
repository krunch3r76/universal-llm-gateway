"""Charter closeout + conveyor observation emitters."""

from __future__ import annotations

from scripts.model_manager.observation_event import _emit


async def emit_manage_charter_closeout_rendered(
    *,
    root: str,
    reason: str,
    sidecar_uri: str,
    window_count: int,
    friction_count: int,
) -> None:
    """Arc closeout sidecar + bus summary were rendered for an enrolled root."""
    await _emit(
        "manage.charter.closeout.rendered",
        {
            "root": root,
            "reason": reason,
            "sidecar_uri": sidecar_uri,
            "window_count": window_count,
            "friction_count": friction_count,
        },
    )


async def emit_manage_charter_conveyor_enrolled(
    *,
    root: str,
    friction_id: int,
    todo_slug: str,
    conveyor_root: str,
) -> None:
    """Follow-on todo appended to the standing friction conveyor."""
    await _emit(
        "manage.charter.conveyor.enrolled",
        {
            "root": root,
            "friction_id": friction_id,
            "todo_slug": todo_slug,
            "conveyor_root": conveyor_root,
        },
    )


async def emit_manage_charter_conveyor_stale(
    *,
    friction_id: int,
    todo_slug: str,
    root: str,
    ticks_idle: int,
) -> None:
    """Conveyor enrollment demoted after CONVEYOR_STALE_TICKS without dispatch."""
    await _emit(
        "manage.charter.conveyor.stale",
        {
            "friction_id": friction_id,
            "todo_slug": todo_slug,
            "root": root,
            "ticks_idle": ticks_idle,
        },
    )


async def emit_manage_charter_conveyor_enroll_failed(
    *,
    root: str,
    window_index: int,
    error: str,
    minted_count: int,
) -> None:
    """Harvest minted follow-ons but conveyor enroll raised — not silent."""
    await _emit(
        "manage.charter.conveyor.enroll_failed",
        {
            "root": root,
            "window_index": window_index,
            "error": error,
            "minted_count": minted_count,
        },
    )


__all__ = [
    "emit_manage_charter_closeout_rendered",
    "emit_manage_charter_conveyor_enrolled",
    "emit_manage_charter_conveyor_enroll_failed",
    "emit_manage_charter_conveyor_stale",
]
