"""Gate 1: refuse force=true on busy drain unless force_reason (or service exempt)."""

from __future__ import annotations

import os
from typing import Any

from universal_logging import get_logger

from scripts.model_manager.observation_event import emit_manage_restart_force_applied

from .busy_service_scope import extract_holder
from .busy_work_summary import format_active_work_summary
from .restart_drain import GATED_ACTIONS, RestartDrainGate
from .restart_drain_force_notice import (
    build_force_gate_notice_body,
    resolve_notice_thread_id,
    schedule_force_gate_notice,
)

logger = get_logger(__name__)


def _schedule_force_gate_notice(**kwargs: Any) -> None:
    _schedule_force_gate_notice(**kwargs)
    from .owed_wake_ledger import maybe_flush_pending_owed_wakes

    maybe_flush_pending_owed_wakes()


REFUSE_REASON = "force_requires_reason"
REFUSE_IN_FLIGHT = "force_refused_in_flight"


def force_exempt_services() -> frozenset[str]:
    """Services allowed to force through G1 busy without ``force_reason`` (default none)."""
    raw = os.environ.get("MANAGE_FORCE_EXEMPT_SERVICES", "")
    if not raw.strip():
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def force_reason_accepted(force_reason: str | None) -> bool:
    return bool(force_reason and str(force_reason).strip())


async def g1_busy_report_entry(
    gate: RestartDrainGate, service: str
) -> dict[str, Any]:
    report = await gate.busy_report([service])
    entry = report.get(service)
    return entry if isinstance(entry, dict) else {}


def g1_blocks_force_without_reason(entry: dict[str, Any]) -> bool:
    """True when Gate 1 would defer a non-force restart (incl. probe_error fail-closed)."""
    return bool(entry.get("restart_would_defer"))


async def refuse_force_without_reason(
    gate: RestartDrainGate,
    service: str,
    action: str,
    *,
    entry: dict[str, Any],
    caller_thread_id: str | None = None,
    caller_dispatch_id: str | None = None,
) -> dict[str, Any]:
    work = entry.get("active_work")
    active_work = work if isinstance(work, dict) else {}
    holder = extract_holder(active_work)
    summary = format_active_work_summary(active_work)
    if not summary and isinstance(entry.get("active_work_summary"), str):
        summary = str(entry["active_work_summary"])
    body = build_force_gate_notice_body(
        outcome="refused",
        service=service,
        action=action,
        force_reason=None,
        busy_summary=summary,
        holder=holder,
    )
    thread_id = resolve_notice_thread_id(
        caller_thread_id=caller_thread_id,
        caller_dispatch_id=caller_dispatch_id,
        holder=holder,
    )
    _schedule_force_gate_notice(
        thread_id=thread_id,
        subject=f"manage force refused ({service} {action})",
        body=body,
    )
    return {
        "status": "refused",
        "status_code": 409,
        "reason": REFUSE_REASON,
        "force_reason": "",
        "service": service,
        "action": action,
        "busy": bool(entry.get("busy")),
        "restart_would_defer": bool(entry.get("restart_would_defer")),
        "determination": entry.get("determination"),
        "holder": holder,
        "active_work_summary": summary,
        "active_work": active_work,
    }


async def audit_and_notice_forced_restart(
    *,
    service: str,
    action: str,
    force_reason: str,
    entry: dict[str, Any],
    caller_thread_id: str | None = None,
    caller_dispatch_id: str | None = None,
) -> None:
    reason = force_reason.strip()
    work = entry.get("active_work")
    active_work = work if isinstance(work, dict) else {}
    holder = extract_holder(active_work)
    summary = format_active_work_summary(active_work)
    await emit_manage_restart_force_applied(
        service=service,
        action=action,
        force_reason=reason,
        caller_dispatch_id=caller_dispatch_id,
    )
    body = build_force_gate_notice_body(
        outcome="applied",
        service=service,
        action=action,
        force_reason=reason,
        busy_summary=summary,
        holder=holder,
    )
    thread_id = resolve_notice_thread_id(
        caller_thread_id=caller_thread_id,
        caller_dispatch_id=caller_dispatch_id,
        holder=holder,
    )
    _schedule_force_gate_notice(
        thread_id=thread_id,
        subject=f"manage force restart ({service} {action})",
        body=body,
    )
    logger.info(
        "force restart of %s (%s) with reason: %s",
        service,
        action,
        reason[:200],
    )


def _usable_count(raw: Any) -> int | None:
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def mcp_in_flight_blocks_force(
    work: dict[str, Any],
    *,
    self_call_counted: bool,
) -> tuple[bool, int | None, int | None]:
    """Return whether a busy mcp force must be refused, plus n and others.

    ``n`` is ``mcp.in_flight`` when that value is a non-bool int. ``others``
    subtracts one when ``self_call_counted`` marks this manage call as that
    in-flight request. Missing ``life_hot`` fails closed.
    """
    mcp = work.get("mcp")
    n: int | None = None
    others: int | None = None
    refuse = False
    if not isinstance(mcp, dict):
        refuse = True
    else:
        if "life_hot" not in mcp or mcp.get("life_hot") is not True:
            refuse = True
        n = _usable_count(mcp.get("in_flight"))
        if n is None:
            refuse = True
        else:
            if self_call_counted and n < 1:
                refuse = True
            others = n - (1 if self_call_counted else 0)
    if "mcp_probe_error" in work:
        refuse = True
    busy_reasons = work.get("busy_reasons")
    if isinstance(busy_reasons, list) and "cdp_ask_live" in busy_reasons:
        refuse = True
    cdp = work.get("cdp_ask")
    if isinstance(cdp, dict):
        running = _usable_count(cdp.get("running_count"))
        if "running_count" not in cdp or running is None or running > 0:
            refuse = True
    if busy_reasons != ["mcp_session_hot"]:
        refuse = True
    if others is not None and others > 0:
        refuse = True
    return refuse, n, others


async def refuse_force_in_flight(
    service: str,
    action: str,
    *,
    entry: dict[str, Any],
    force_reason: str,
    n: int | None,
    others: int | None,
    caller_thread_id: str | None = None,
    caller_dispatch_id: str | None = None,
) -> dict[str, Any]:
    """409 when mcp force is not life_hot-only with no other in-flight work."""
    work = entry.get("active_work")
    active_work = work if isinstance(work, dict) else {}
    holder = extract_holder(active_work)
    summary = format_active_work_summary(active_work)
    if not summary and isinstance(entry.get("active_work_summary"), str):
        summary = str(entry["active_work_summary"])
    body = build_force_gate_notice_body(
        outcome="refused",
        service=service,
        action=action,
        force_reason=force_reason,
        busy_summary=summary,
        holder=holder,
    )
    body = f"{body}\nreason={REFUSE_IN_FLIGHT}"
    thread_id = resolve_notice_thread_id(
        caller_thread_id=caller_thread_id,
        caller_dispatch_id=caller_dispatch_id,
        holder=holder,
    )
    _schedule_force_gate_notice(
        thread_id=thread_id,
        subject=f"manage force refused ({service} {action})",
        body=body,
    )
    return {
        "status": "refused",
        "status_code": 409,
        "reason": REFUSE_IN_FLIGHT,
        "force_reason": force_reason.strip(),
        "n": n,
        "others": others,
        "service": service,
        "action": action,
    }


async def _admit_forced_restart(
    *,
    service: str,
    action: str,
    force_reason: str,
    entry: dict[str, Any],
    caller_thread_id: str | None,
    caller_dispatch_id: str | None,
) -> None:
    await audit_and_notice_forced_restart(
        service=service,
        action=action,
        force_reason=force_reason,
        entry=entry,
        caller_thread_id=caller_thread_id,
        caller_dispatch_id=caller_dispatch_id,
    )


async def enforce_force_reason_if_needed(
    gate: RestartDrainGate,
    action: str,
    service: str,
    *,
    force: bool,
    force_reason: str | None,
    caller_thread_id: str | None = None,
    caller_dispatch_id: str | None = None,
    caller_requested: bool = False,
    self_call_counted: bool = False,
) -> dict[str, Any] | None:
    """Return a refusal envelope, or None when the caller may proceed.

    ``self_call_counted`` is true when the MCP manage tool counted this
    restart in ``mcp.in_flight``. Busy mcp admits only when that leaves
    ``others == 0``, ``life_hot`` is true, and the sole busy reason is
    ``mcp_session_hot``.
    """
    if not caller_requested:
        return None
    if not force or action not in GATED_ACTIONS:
        return None
    if service == "git_integration_worker":
        return None
    if service in force_exempt_services():
        return None
    entry = await g1_busy_report_entry(gate, service)
    from .owed_wake_ledger import maybe_flush_pending_owed_wakes

    maybe_flush_pending_owed_wakes()
    if not g1_blocks_force_without_reason(entry):
        return None
    if service != "mcp":
        if force_reason_accepted(force_reason):
            await _admit_forced_restart(
                service=service,
                action=action,
                force_reason=str(force_reason),
                entry=entry,
                caller_thread_id=caller_thread_id,
                caller_dispatch_id=caller_dispatch_id,
            )
            return None
        return await refuse_force_without_reason(
            gate,
            service,
            action,
            entry=entry,
            caller_thread_id=caller_thread_id,
            caller_dispatch_id=caller_dispatch_id,
        )
    if not force_reason_accepted(force_reason):
        return await refuse_force_without_reason(
            gate,
            service,
            action,
            entry=entry,
            caller_thread_id=caller_thread_id,
            caller_dispatch_id=caller_dispatch_id,
        )
    work = entry.get("active_work")
    active_work = work if isinstance(work, dict) else {}
    blocked, n, others = mcp_in_flight_blocks_force(
        active_work, self_call_counted=self_call_counted
    )
    if blocked:
        return await refuse_force_in_flight(
            service,
            action,
            entry=entry,
            force_reason=str(force_reason).strip(),
            n=n,
            others=others,
            caller_thread_id=caller_thread_id,
            caller_dispatch_id=caller_dispatch_id,
        )
    await _admit_forced_restart(
        service=service,
        action=action,
        force_reason=str(force_reason),
        entry=entry,
        caller_thread_id=caller_thread_id,
        caller_dispatch_id=caller_dispatch_id,
    )
    return None
