"""Charter closeout observation emitters."""

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


__all__ = [
    "emit_manage_charter_closeout_rendered",
]
