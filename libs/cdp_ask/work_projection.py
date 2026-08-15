"""Sealed execution and restart-drain read models for the cdp-ask satellite.

The execution store supplies recorded pending/running records.  This module
joins those records with cached occupancy only when a caller explicitly asks
for a read model, keeping stream admission independent from browser sensing.
No function in this module performs Chrome or registry census I/O.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Protocol

from admission_common.qualified_scalar import (
    AuthorityClass,
    QualifiedScalar,
    SurfaceDecl,
    seal,
)

from cdp_ask.lane_admission import (
    ADVISOR_RESERVE,
    LANE_HARD_LIMIT,
    LANE_SOFT_LIMIT,
    admission_regime,
    count_by_purpose_class,
    effective_abs_hard,
)

_ACTIVE_WORK_SNAPSHOT = "active_work_snapshot"
_DRAIN_STATE_SNAPSHOT = "drain_state_snapshot"
_RUNNING_COUNT_SCOPE = "cdp_ask execution store, pending/running streams"
_OPEN_ATTACHMENT_COUNT_SCOPE = (
    "CSE-bearing live CDP browser-host attachments, this host"
)
_LIVE_CSE_TARGET_COUNT_SCOPE = "qualifying type=page CSE targets, this host"
_LIVE_PORT_COUNT_SCOPE = (
    "live CDP registry-pool ports responding to /json/version, this host"
)
_LIVE_CSE_COUNT_SCOPE = (
    "unique normalized CSE session URLs on qualifying page targets, this host"
)
_ADMISSION_COUNT_SCOPE = "running/stream admissions, this host (soft=2 hard=3)"
_REGISTRY_CAPACITY_SCOPE = "active registry Chrome hosts (ports/profiles), this host"
_EFFECTIVE_COUNT_SCOPE = "restart-drain recorded execution count; NOT admission"
_REGISTRY_SOURCE = "cse-session-registry"


class OccupancyProvider(Protocol):
    """Minimal cached occupancy interface required by the restart-drain read model.

    Implementations expose only already-sampled state, never browser or registry
    census I/O, so request handlers remain bounded.
    """

    def snapshot(self) -> dict[str, Any]: ...

    def safe_busy(self, running_count: int) -> bool: ...


def _registry_projection(registration_id: str | None) -> dict[str, str | None]:
    """Join one execution row with recorded registry URLs and seat metadata."""
    empty = {
        "cdp_url": None,
        "chat_url": None,
        "source": None,
        "parent_thread": None,
        "mission_kind": None,
    }
    if not registration_id:
        return empty
    from claude_bundles import cdp_registry

    chat_url = cdp_registry.chat_url_for_registration(registration_id)
    cdp_url: str | None = None
    parent_thread: str | None = None
    mission_kind: str | None = None
    for lane in cdp_registry.list_active():
        if lane.registration_id == registration_id:
            cdp_url = lane.cdp_url
            parent_thread = getattr(lane, "parent_thread", None)
            mission_kind = getattr(lane, "mission_kind", None)
            break
    if not chat_url and not cdp_url:
        return {
            **empty,
            "parent_thread": parent_thread,
            "mission_kind": mission_kind,
        }
    return {
        "cdp_url": cdp_url,
        "chat_url": chat_url,
        "source": _REGISTRY_SOURCE,
        "parent_thread": parent_thread,
        "mission_kind": mission_kind,
    }


def active_rows(records: Iterable[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Render pending/running records as rows for admission and drain views."""
    active = [record for record in records if record.status in {"pending", "running"}]
    execution_ids = [record.execution_id for record in active]
    rows: list[dict[str, Any]] = []
    for record in active:
        projection = _registry_projection(record.registration_id)
        rows.append(
            {
                "execution_id": record.execution_id,
                "registration_id": record.registration_id,
                "holder": record.holder,
                "purpose": record.purpose,
                "status": record.status,
                "cdp_url": projection["cdp_url"],
                "chat_url": projection["chat_url"],
                "source": projection["source"],
                "parent_thread": record.parent_thread
                or projection.get("parent_thread"),
                "mission_kind": record.mission_kind
                or projection.get("mission_kind"),
            }
        )
    return rows, execution_ids


def admission_projection(
    rows: list[dict[str, Any]], execution_ids: list[str]
) -> tuple[dict[str, Any], SurfaceDecl]:
    """Build the O(1) recorded execution/admission projection and publication seal metadata."""
    running_count = len(execution_ids)
    seat_count, other_count = count_by_purpose_class(rows)
    regime = admission_regime(seat_count)
    effective_hard = effective_abs_hard(seat_count)
    admission_count = running_count
    payload: dict[str, Any] = {
        "busy": running_count > 0,
        "execution_ids": execution_ids,
        "rows": rows,
        "soft_limit": LANE_SOFT_LIMIT,
        "hard_limit": LANE_HARD_LIMIT,
        "free_slots": max(0, effective_hard - admission_count),
        "at_soft_limit": admission_count >= LANE_SOFT_LIMIT,
        "at_hard_limit": admission_count >= effective_hard,
        "seat_count": seat_count,
        "other_count": other_count,
        "advisor_reserve": ADVISOR_RESERVE,
        "admission_regime": regime,
        "effective_abs_hard": effective_hard,
    }
    payload.update(
        QualifiedScalar(
            value=running_count,
            scope=_RUNNING_COUNT_SCOPE,
            authority=AuthorityClass.RECORDED,
        ).emit("running_count")
    )
    payload.update(
        QualifiedScalar(
            value=admission_count,
            scope=_ADMISSION_COUNT_SCOPE,
            authority=AuthorityClass.RECORDED,
        ).emit("admission_count")
    )
    decl = SurfaceDecl(_ACTIVE_WORK_SNAPSHOT)
    decl.plain("busy", reason="derived boolean: running_count > 0")
    decl.plain("soft_limit", reason="configured stream admission constant")
    decl.plain("hard_limit", reason="configured stream admission constant")
    decl.plain("free_slots", reason="derived: effective_abs_hard - admission_count")
    decl.plain("at_soft_limit", reason="derived: admission_count >= soft_limit")
    decl.plain("at_hard_limit", reason="derived: admission_count >= effective_abs_hard")
    decl.plain("seat_count", reason="derived: pending/running seat-purpose rows")
    decl.plain("other_count", reason="derived: pending/running non-seat rows")
    decl.plain("advisor_reserve", reason="configured reserved advisor slot count")
    decl.plain(
        "admission_regime",
        reason="additive when seat_count > hard_limit - reserve else carved",
    )
    decl.plain(
        "effective_abs_hard",
        reason="regime-aware absolute stream ceiling",
    )
    return payload, decl


def drain_projection(
    rows: list[dict[str, Any]],
    execution_ids: list[str],
    occupancy: OccupancyProvider | None,
) -> dict[str, Any]:
    """Build restart state from recorded executions plus diagnostic occupancy."""
    payload, _ = admission_projection(rows, execution_ids)
    occupancy_data = (
        occupancy.snapshot()
        if occupancy is not None
        else {
            "live_cse_count": None,
            "open_attachment_count": None,
            "live_cse_target_count": None,
            "live_port_count": None,
            "registry_capacity_count": None,
            "observed_at": None,
            "observation_age_s": None,
            "freshness": "unobserved",
            "error": "occupancy projection not bound",
            "source": None,
        }
    )
    freshness = str(occupancy_data.get("freshness") or "unobserved")
    live_cse_count = occupancy_data.get("live_cse_count")
    open_attachment_count = occupancy_data.get("open_attachment_count")
    if open_attachment_count is None:
        open_attachment_count = live_cse_count
    live_cse_target_count = occupancy_data.get("live_cse_target_count")
    if live_cse_target_count is None:
        live_cse_target_count = live_cse_count
    live_port_count = occupancy_data.get("live_port_count")
    registry_capacity_count = occupancy_data.get("registry_capacity_count")
    running_count = len(execution_ids)
    effective = running_count
    busy_reason = "execution" if running_count > 0 else "idle"
    payload.update(
        {
            "busy": running_count > 0,
            "drain_busy_reason": busy_reason,
            "occupancy_freshness": freshness,
            "occupancy_source": occupancy_data.get("source"),
            "occupancy_error": occupancy_data.get("error"),
            "occupancy_observed_at": (
                datetime.fromtimestamp(
                    float(occupancy_data["observed_at"]),
                    tz=UTC,
                ).isoformat()
                if occupancy_data.get("observed_at") is not None
                else None
            ),
        }
    )
    payload.update(
        QualifiedScalar(
            value=live_cse_count,
            scope=_LIVE_CSE_COUNT_SCOPE,
            authority=AuthorityClass.OBSERVED,
        ).emit("live_cse_count")
    )
    payload.update(
        QualifiedScalar(
            value=open_attachment_count,
            scope=_OPEN_ATTACHMENT_COUNT_SCOPE,
            authority=AuthorityClass.OBSERVED,
        ).emit("open_attachment_count")
    )
    payload.update(
        QualifiedScalar(
            value=live_cse_target_count,
            scope=_LIVE_CSE_TARGET_COUNT_SCOPE,
            authority=AuthorityClass.OBSERVED,
        ).emit("live_cse_target_count")
    )
    payload.update(
        QualifiedScalar(
            value=live_port_count,
            scope=_LIVE_PORT_COUNT_SCOPE,
            authority=AuthorityClass.OBSERVED,
        ).emit("live_port_count")
    )
    payload.update(
        QualifiedScalar(
            value=registry_capacity_count,
            scope=_REGISTRY_CAPACITY_SCOPE,
            authority=AuthorityClass.RECORDED,
        ).emit("registry_capacity_count")
    )
    payload.update(
        QualifiedScalar(
            value=effective,
            scope=_EFFECTIVE_COUNT_SCOPE,
            authority=AuthorityClass.RECORDED,
        ).emit("effective_count")
    )
    payload.update(
        QualifiedScalar(
            value=occupancy_data.get("observation_age_s"),
            scope="age of the latest CDP occupancy observation, this host",
            authority=AuthorityClass.OBSERVED,
        ).emit("occupancy_age_s")
    )
    decl = SurfaceDecl(_DRAIN_STATE_SNAPSHOT)
    for name, reason in {
        "busy": "derived from pending/running execution rows",
        "drain_busy_reason": "derived drain-state reason",
        "occupancy_freshness": "projection freshness state",
        "occupancy_source": "projection source label",
        "occupancy_error": "last projection sensor error",
        "occupancy_observed_at": "latest observation timestamp",
    }.items():
        decl.plain(name, reason=reason)
    for name, reason in {
        "soft_limit": "configured stream admission constant",
        "hard_limit": "configured stream admission constant",
        "free_slots": "derived admission capacity",
        "at_soft_limit": "derived: admission_count >= soft_limit",
        "at_hard_limit": "derived: admission_count >= effective_abs_hard",
        "seat_count": "derived: pending/running seat-purpose rows",
        "other_count": "derived: pending/running non-seat rows",
        "advisor_reserve": "configured reserved advisor slot count",
        "admission_regime": "derived purpose-aware admission regime",
        "effective_abs_hard": "regime-aware absolute stream ceiling",
    }.items():
        decl.plain(name, reason=reason)
    return seal(payload, decl)
