"""Sealed execution and restart-drain read models for the cdp-ask satellite.

The execution store supplies recorded pending/running records for admission.
The restart-drain read model derives ``busy`` from the registry rows' durable
``execution_state`` (``claude_bundles.cdp_registry.execution_state``) handed
in by the caller — the same file the next cdp_ask process and the manage
witness read — so memory that a recycle erases never decides a recycle.
This module joins those inputs with cached occupancy only when a caller
explicitly asks for a read model. No function here performs Chrome or
registry census I/O.
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
    ADMISSION_COUNT_SCOPE,
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
_ADMISSION_COUNT_SCOPE = ADMISSION_COUNT_SCOPE
_REGISTRY_CAPACITY_SCOPE = (
    "active+retained registry Chrome hosts (ports/profiles), this host"
)
_EFFECTIVE_COUNT_SCOPE = (
    "restart-drain in-flight count: registry execution_state rows + unseated "
    "pending executions; NOT admission"
)
_IN_FLIGHT_COUNT_SCOPE = (
    "registry active.json rows whose execution_state is seated/streaming, this host"
)
_REGISTRY_SOURCE = "cse-session-registry"
_BUSY_SOURCE = "cdp-registry active.json execution_state"


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
        holder_value = record.holder
        chat = projection.get("chat_url")
        if chat:
            from claude_bundles.holder_strings import (
                format_cowork_cse_holder,
                holder_id_from_chat_url,
            )

            hid = holder_id_from_chat_url(str(chat))
            if hid:
                holder_value = format_cowork_cse_holder(hid)
        rows.append(
            {
                "execution_id": record.execution_id,
                "registration_id": record.registration_id,
                "holder": holder_value,
                "purpose": record.purpose,
                "status": record.status,
                "stream_state": record.status,
                "cdp_url": projection["cdp_url"],
                "chat_url": projection["chat_url"],
                "source": projection["source"],
                "parent_thread": record.parent_thread
                or projection.get("parent_thread"),
                "mission_kind": record.mission_kind or projection.get("mission_kind"),
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
    decl.plain(
        "free_slots",
        reason="stream admission: effective_abs_hard - admission_count; not X/window mint room",
    )
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
    decl.plain(
        "seat",
        reason="hygiene discriminator on seat_rows identity projection",
    )
    return payload, decl


def in_flight_summary(
    in_flight: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Flatten ``in_flight_rows`` output into drain rows and witness holders."""
    out: list[dict[str, Any]] = []
    for rid, row in in_flight.items():
        entry = row.get("execution_state") or {}
        out.append(
            {
                "registration_id": rid,
                "execution_id": entry.get("execution_id"),
                "state": entry.get("state"),
                "kind": entry.get("kind") or "execution",
                "started_at": entry.get("started_at"),
                "holder": row.get("holder"),
                "purpose": row.get("purpose"),
                "chat_url": row.get("chat_url"),
                "port": row.get("port"),
                "row_status": row.get("status"),
            }
        )
    return out


def holders_from_in_flight(in_flight: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Census-shaped holders (``kind``/``op_id``/``subject_preview``) for the witness."""
    holders: list[dict[str, Any]] = []
    for item in in_flight:
        eid = str(item.get("execution_id") or "").strip()
        if not eid:
            continue
        subject = " ".join(
            str(item[k]).strip() for k in ("holder", "purpose") if item.get(k)
        )
        rec: dict[str, Any] = {
            "kind": str(item.get("kind") or "execution"),
            "op_id": eid,
        }
        if subject:
            rec["subject_preview"] = subject
        holders.append(rec)
    return holders


def drain_projection(
    rows: list[dict[str, Any]],
    execution_ids: list[str],
    occupancy: OccupancyProvider | None,
    *,
    in_flight: list[dict[str, Any]] | None = None,
    unseated_pending: list[str] | None = None,
    registry_error: str | None = None,
) -> dict[str, Any]:
    """Build restart state: ``busy`` from durable in-flight rows, occupancy as diagnostics.

    ``in_flight`` is ``in_flight_summary(in_flight_rows(active.json))``;
    ``unseated_pending`` names this process's executions admitted before their
    row exists. ``registry_error`` set means the row set could not be read —
    busy fail-closed, reason ``registry_unreadable``.
    """
    payload, _ = admission_projection(rows, execution_ids)
    in_flight = list(in_flight or [])
    unseated = list(unseated_pending or [])
    from claude_bundles.cdp_registry.execution_state import (
        in_flight_entry_contributes_restart_gate_busy,
    )

    restart_gate_in_flight = [
        item
        for item in in_flight
        if in_flight_entry_contributes_restart_gate_busy(
            {
                "kind": item.get("kind") or "execution",
                "execution_id": item.get("execution_id"),
                "state": item.get("state"),
            }
        )
    ]
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
    effective = len(restart_gate_in_flight) + len(unseated)
    if registry_error is not None:
        busy_reason = "registry_unreadable"
    elif restart_gate_in_flight:
        busy_reason = "in_flight_recorded"
    elif unseated:
        busy_reason = "unseated_pending"
    else:
        busy_reason = "idle"
    payload.update(
        {
            "busy": busy_reason != "idle",
            "drain_busy_reason": busy_reason,
            "busy_source": _BUSY_SOURCE,
            "registry_error": registry_error,
            "in_flight": in_flight,
            "unseated_pending": unseated,
            "holders": holders_from_in_flight(restart_gate_in_flight),
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
            value=len(in_flight),
            scope=_IN_FLIGHT_COUNT_SCOPE,
            authority=AuthorityClass.RECORDED,
        ).emit("in_flight_count")
    )
    payload.update(
        QualifiedScalar(
            value=occupancy_data.get("observation_age_s"),
            scope="age of the latest CDP occupancy observation, this host",
            authority=AuthorityClass.OBSERVED,
        ).emit("occupancy_age_s")
    )
    decl = SurfaceDecl(_DRAIN_STATE_SNAPSHOT)
    decl.transcript(
        "in_flight", reason="registry execution_state in-flight rows verbatim"
    )
    decl.transcript(
        "unseated_pending", reason="execution ids admitted before a row exists"
    )
    decl.transcript("holders", reason="census-shaped holders derived from in_flight")
    for name, reason in {
        "busy": "derived: restart-gate in_flight rows or unseated pending non-empty, or registry unreadable",
        "drain_busy_reason": "derived drain-state reason",
        "busy_source": "authority the busy flag derives from",
        "registry_error": "last registry read error, else null",
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
