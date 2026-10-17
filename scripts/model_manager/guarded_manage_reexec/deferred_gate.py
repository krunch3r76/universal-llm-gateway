"""A2a per-service busy_status gate for deferred manage recycle (child seat)."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .checks import RefuseFinding, observe_manage_inflight

RECYCLE_FLEET_SERVICES = (
    "stargate",
    "gateway",
    "rag",
    "agent_bus",
    "event_service",
    "mcp",
    "git_integration_worker",
    "cloud_proxy",
    "cortex_api",
)

_ACTIVE_COUNT_ONLY = re.compile(r"^active_count=\d+$")
ManageCall = Callable[..., dict[str, Any]]


def _manage(
    manage_call: ManageCall,
    method: str,
    params: dict[str, Any] | None,
    sock_path: str | None,
) -> dict[str, Any]:
    if sock_path:
        return manage_call(method, params or {}, sock_path=sock_path)
    return manage_call(method, params or {})


def mcp_row_matches_exception_b(row: dict[str, Any]) -> bool:
    holder = row.get("holder")
    if not isinstance(holder, dict):
        return False
    return (
        holder.get("kind") == "mcp_life_session"
        and holder.get("subject_preview") == "cdp_ask running=1"
        and holder.get("dispatch_id") is None
    )


def stargate_row_matches_exception_c(
    stargate_row: dict[str, Any], *, mcp_b: bool
) -> bool:
    if not mcp_b:
        return False
    if stargate_row.get("holder") is not None:
        return False
    if stargate_row.get("probe_error") is not None:
        return False
    summary = str(stargate_row.get("active_work_summary") or "")
    return bool(_ACTIVE_COUNT_ONLY.fullmatch(summary))


def run_recycle_step1_per_service_gate(
    manage_call: ManageCall,
    *,
    parent_dispatch_id: str,
    sock_path: str | None = None,
) -> RefuseFinding | None:
    del parent_dispatch_id  # (a) inert at child gate — parent id is not a pass.
    rows: dict[str, dict[str, Any]] = {}
    for name in RECYCLE_FLEET_SERVICES:
        payload = _manage(manage_call, "busy_status", {"service": name}, sock_path)
        if payload.get("status") == "error":
            return RefuseFinding(
                reason="busy_status_unobservable",
                offenders=[{"service": name, "payload": payload}],
            )
        rows[name] = payload

    for name, row in rows.items():
        if row.get("restart_intent") is not None:
            return RefuseFinding(
                reason="nonterminal_restart_intent",
                offenders=[{"service": name, "service_row": row}],
            )
        if row.get("restart_window") is not None:
            return RefuseFinding(
                reason="open_restart_window",
                offenders=[{"service": name, "service_row": row}],
            )
        if "process" in row:
            finding, _, _, _ = observe_manage_inflight(row)
            if finding is not None:
                finding.offenders[0]["service"] = name
                return finding

    mcp_row = rows["mcp"]
    mcp_b = mcp_row_matches_exception_b(mcp_row)
    for name, row in rows.items():
        holder = row.get("holder")
        if name == "mcp":
            if (row.get("busy") or holder is not None) and not mcp_b:
                return RefuseFinding(
                    reason="step1_mcp_not_exception_b",
                    offenders=[{"service": "mcp", "row": row}],
                )
            continue
        if name == "stargate":
            if holder is not None or (
                row.get("busy")
                and not stargate_row_matches_exception_c(row, mcp_b=mcp_b)
            ):
                return RefuseFinding(
                    reason="step1_stargate_not_exception_c",
                    offenders=[{"service": "stargate", "row": row}],
                )
            continue
        if name in ("git_integration_worker", "agent_bus"):
            if row.get("busy") or holder is not None:
                return RefuseFinding(
                    reason="step1_giw_agent_bus_busy",
                    offenders=[{"service": name, "row": row}],
                )
            continue
        if holder is not None:
            return RefuseFinding(
                reason="step1_unclassified_holder",
                offenders=[{"service": name, "row": row}],
            )
    return None
