"""Substrate facts for kernel admission — typed ``EnvSnapshot`` assembly."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from universal_logging import get_logger

from .admission import EnvFacts
from .attendance import resolve_attendance
from .env_predicates import EnvironmentSnapshot

logger = get_logger(__name__)

SatelliteState = Literal["up", "down", "unknown"]
_SATELLITE_PROBE_TIMEOUT_S = 2.0
_LOCALHOST = frozenset({"localhost", "127.0.0.1", "::1"})


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

    def restart_shaped_for_root(self, root_id: str) -> bool:
        """True when the ledger pickup names a GIW sync_restart manage step."""
        from .admission import next_pickup_is_restart_from_holder

        pointer = self.scoreboard_pointer.get(root_id, "")
        _ = pointer  # scoreboard is advisory; restart shape comes from bus tip meta
        tip = self.bus_tip_meta.get(root_id) or {}
        pickup_lines = tip.get("next_pickup") or []
        if isinstance(pickup_lines, str):
            pickup_lines = [pickup_lines]
        return any(
            next_pickup_is_restart_from_holder(str(item))
            for item in pickup_lines
            if item
        )

    def facts_for_root(self, root_id: str, *, has_wip: bool = False) -> EnvFacts:
        attendance = self.attendance_by_root.get(root_id)
        if attendance is None:
            logger.warning(
                "attendance missing from env snapshot root_id=%s — defaulting attended",
                root_id,
            )
            attendance = "attended"
        return EnvFacts(
            substrate_up=self.substrate_up(),
            has_wip=has_wip,
            attendance=attendance,
            propagation_residue=self.propagation_residue,
            giw_holder_lease=self.giw_holder_lease,
            restart_shaped=self.restart_shaped_for_root(root_id),
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


async def _probe_cdp_health(host: str, port: int, base_url: str) -> SatelliteState:
    """GET ``/health`` on the cdp-ask satellite; map to up|down|unknown.

    Connect/refused → ``down`` (P4-AC1 outage signal). Timeout / unexpected
    transport → ``unknown`` so merely-slow consults do not flip substrate_up
    false (P4-AC4). ``status!=ok`` / non-200 → ``down``.
    """
    from transport_utils import make_async_client

    try:
        async with make_async_client(
            base_url, timeout=_SATELLITE_PROBE_TIMEOUT_S
        ) as client:
            resp = await client.get("/health")
    except httpx.ConnectError:
        return "down"
    except httpx.TimeoutException:
        logger.warning(
            "cdp-ask health probe timed out host=%s port=%s — treating unknown",
            host,
            port,
        )
        return "unknown"
    except Exception as exc:  # noqa: BLE001 — probe must not crash the tick
        logger.warning(
            "cdp-ask health probe failed (%s) host=%s port=%s — treating unknown",
            type(exc).__name__,
            host,
            port,
        )
        return "unknown"

    if resp.status_code != 200:
        return "down"
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return "down"
    if not isinstance(body, dict) or body.get("status") != "ok":
        return "down"
    return "up"


async def probe_satellite_health() -> dict[str, SatelliteState]:
    """Live ``{cdp, project_ask}`` health from PROJECT_ASK_URL ``/health``.

    Both keys share the cdp-ask satellite (same URL). Unconfigured URL →
    ``unknown`` (substrate_up stays true — fail-open when the satellite is not
    part of the fleet).
    """
    from ..service_config import cdp_ask_url_config

    cfg = cdp_ask_url_config()
    if cfg is None:
        return {"cdp": "unknown", "project_ask": "unknown"}
    host, port, base_url = cfg
    probe_host = "127.0.0.1" if host in _LOCALHOST else host
    # Rebuild base when localhost-aliased so the client hits the loopback probe.
    if probe_host != host:
        base_url = f"http://{probe_host}:{port}"
    state = await _probe_cdp_health(probe_host, port, base_url)
    return {"cdp": state, "project_ask": state}


async def build_env_snapshot(
    *,
    root_ids: list[str],
    env_half: EnvironmentSnapshot | None = None,
    in_flight: list[dict[str, Any]] | None = None,
) -> EnvSnapshot:
    """Assemble kernel env facts from tick-scoped substrate reads."""
    giw_lease = {"held": False, "holder": None, "residue": None}
    propagation: dict[str, Any] = {"kind": None, "detail": None}
    if env_half is not None:
        giw_src = env_half.sources.get("giw_live_hold")
        if giw_src is not None and hasattr(giw_src, "payload"):
            payload = giw_src.payload or {}
            if isinstance(payload, dict):
                giw_lease["held"] = bool(payload.get("held"))
                giw_lease["holder"] = payload.get("holder")
                giw_lease["residue"] = payload.get("residue")
            elif isinstance(payload, bool):
                giw_lease["held"] = payload
        drain = env_half.sources.get("giw_drain_intent")
        if drain is not None and hasattr(drain, "payload"):
            intent = drain.payload
            propagation["kind"] = "git_integration_worker"
            propagation["detail"] = "sync_restart"
            if intent is not None:
                propagation["intent"] = str(getattr(intent, "intent_id", intent))

    resolved, satellite_health = await asyncio.gather(
        asyncio.gather(*(resolve_attendance(rid) for rid in root_ids)),
        probe_satellite_health(),
    )
    attendance = dict(zip(root_ids, resolved, strict=True))
    scoreboards = {
        rid: f"cortex://notes/system/threads/{rid}-charter-scoreboard.md"
        for rid in root_ids
    }
    return EnvSnapshot(
        giw_holder_lease=giw_lease,
        propagation_residue=propagation,
        in_flight_windows=in_flight or [],
        satellite_health=dict(satellite_health),
        attendance_by_root=attendance,
        scoreboard_pointer=scoreboards,
        bus_tip_meta={rid: {"has_checkpoint": True, "turn_id": ""} for rid in root_ids},
    )


__all__ = ["EnvSnapshot", "build_env_snapshot", "probe_satellite_health"]
