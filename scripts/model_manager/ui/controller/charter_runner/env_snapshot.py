"""Substrate facts for kernel admission — typed ``EnvSnapshot`` assembly."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .admission import EnvFacts
from .attendance import default_attendance_lookup
from .env_predicates import EnvironmentSnapshot


@dataclass(frozen=True)
class EnvSnapshot:
    """Spec §C.5 fact set for shadow/kernel ticks."""

    giw_holder_lease: dict[str, Any]
    propagation_residue: dict[str, Any]
    in_flight_windows: list[dict[str, Any]]
    satellite_health: dict[str, str]
    attendance_by_root: dict[str, str]
    scoreboard_pointer: dict[str, str]
    bus_tip_meta: dict[str, dict[str, Any]]
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def substrate_up(self) -> bool:
        cdp = self.satellite_health.get("cdp", "unknown")
        if cdp == "down":
            return False
        return True

    def facts_for_root(self, root_id: str, *, has_wip: bool = False) -> EnvFacts:
        attendance = self.attendance_by_root.get(
            root_id, default_attendance_lookup(root_id)
        )
        return EnvFacts(
            substrate_up=self.substrate_up(),
            has_wip=has_wip,
            attendance=attendance,
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "giw_holder_lease": self.giw_holder_lease,
                "propagation_residue": self.propagation_residue,
                "in_flight_windows": self.in_flight_windows,
                "satellite_health": self.satellite_health,
                "attendance_by_root": self.attendance_by_root,
                "scoreboard_pointer": self.scoreboard_pointer,
                "bus_tip_meta": self.bus_tip_meta,
                "observed_at": self.observed_at.isoformat(),
            },
            sort_keys=True,
        )


def build_env_snapshot(
    *,
    root_ids: list[str],
    env_half: EnvironmentSnapshot | None = None,
    in_flight: list[dict[str, Any]] | None = None,
) -> EnvSnapshot:
    """Assemble kernel env facts from tick-scoped substrate reads."""
    giw_lease = {"held": False, "holder": None, "residue": None}
    propagation = {"kind": None, "detail": None}
    if env_half is not None:
        giw_src = env_half.sources.get("giw_live_hold")
        if giw_src is not None and hasattr(giw_src, "payload"):
            payload = giw_src.payload or {}
            if isinstance(payload, dict):
                giw_lease["held"] = bool(payload.get("held"))
                giw_lease["holder"] = payload.get("holder")
        drain = env_half.sources.get("giw_drain_intent")
        if drain is not None and hasattr(drain, "payload"):
            propagation["kind"] = "giw_drain"
            propagation["detail"] = str(drain.payload)

    attendance = {rid: default_attendance_lookup(rid) for rid in root_ids}
    scoreboards = {
        rid: f"cortex://notes/system/threads/{rid}-charter-scoreboard.md"
        for rid in root_ids
    }
    return EnvSnapshot(
        giw_holder_lease=giw_lease,
        propagation_residue=propagation,
        in_flight_windows=in_flight or [],
        satellite_health={"cdp": "up", "project_ask": "up"},
        attendance_by_root=attendance,
        scoreboard_pointer=scoreboards,
        bus_tip_meta={rid: {"has_checkpoint": True, "turn_id": ""} for rid in root_ids},
    )


__all__ = ["EnvSnapshot", "build_env_snapshot"]
