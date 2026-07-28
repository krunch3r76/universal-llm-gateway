"""Park-friction mint path — storm fuse dedup on file + follow-on mint."""

from __future__ import annotations

from typing import Any

from cortex_store.dispatch_ops._friction_enqueue import (
    file_charter_protocol_friction,
    mint_friction_followon,
)

from . import storm_fuse
from .telemetry import emit_storm_fuse_tripped


async def file_park_friction(
    *,
    root_id: str,
    window_index: int,
    note: str,
    tip_gid: str,
    mismatch_class: str,
    category: str = "protocol",
    actionable: bool = True,
) -> int | None:
    """File a conveyor-origin park friction; dedup when fuse holds identical identity."""
    identity = storm_fuse.FuseIdentity(
        category=category,
        tip_gid=tip_gid,
        mismatch_class=mismatch_class,
    )
    existing = storm_fuse.held_friction_id_for_identity(identity)
    if existing is not None:
        return existing

    friction_id = file_charter_protocol_friction(
        root_id=root_id,
        window_index=window_index,
        note=note,
        category=category,
        actionable=actionable,
    )
    if friction_id is None:
        return None

    result = storm_fuse.record_park_friction(identity, friction_id)
    if result.tripped:
        await emit_storm_fuse_tripped(
            root=root_id,
            identity_key=identity.key(),
            category=identity.category,
            tip_gid=identity.tip_gid,
            mismatch_class=identity.mismatch_class,
            consecutive_count=result.consecutive_count,
            held_friction_id=result.held_friction_id or friction_id,
        )
    if result.suppressed:
        return result.held_friction_id
    return friction_id


def mint_followon_with_fuse(
    friction_row: dict[str, Any],
    *,
    root_id: str,
    agent: str = "charter-runner",
    session_id: str = "friction-enqueue",
) -> str | None:
    """Mint G3 follow-on; refuse when friction is quarantined."""
    try:
        friction_id = int(friction_row["id"])
    except (KeyError, TypeError, ValueError):
        friction_id = None
    if friction_id is not None and storm_fuse.is_quarantined(friction_id):
        return None
    return mint_friction_followon(
        friction_row,
        root_id=root_id,
        agent=agent,
        session_id=session_id,
    )


__all__ = ["file_park_friction", "mint_followon_with_fuse"]
