"""Terminal path for ``contract: propagate`` — mint rows and coordinate restart."""

from __future__ import annotations

import asyncio
from typing import Any

from charter_runner_store.propagation_ledger import (
    close_row,
    fail_row,
    mark_harvest_wanted,
    set_defer_reason,
    upsert_open_rows,
)
from implement_admission.propagation_row import PropagationRow

from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_terminal_status,
)
from services.git_integration_worker.cursor_auto.manage_sock import sync_restart_service
from services.git_integration_worker.cursor_auto.propagate_admission import (
    PROPAGATE_CONTRACT,
    admit_propagate_body,
)
from services.git_integration_worker.cursor_auto.propagation_probe import (
    proof_observed,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_bus import CursorBusClient

DEFER_MANAGE_QUEUED_DRAIN = "manage_queued_drain"
DEFER_MANAGE_BUSY_DEFER = "manage_busy_defer"
DEFER_HARVEST_WANTED = "harvest_wanted"

# Operator bind 2026-08-02: cursor-auto executes operator-proxy self-preempt
# restarts rather than harvest_wanted pushback. force remains mcp|cdp_ask only.
_FORCE_ALLOWED_SERVICES = frozenset({"mcp", "cdp_ask"})
_SELF_PREEMPT_MARKERS = (
    "cdp_ask_live",
    "mcp_session_hot",
    "in-flight work",
    "pass force=true",
)
MCP_DISCONNECT_ADVISORY = (
    "MCP will disconnect momentarily — operator-proxy self-preempt; "
    "reconnect after healthy (force lands the container, it does not refresh "
    "the live CSE MCP binding)."
)


def _preempted_work_label(manage_result: dict[str, Any]) -> str:
    """Human-readable label for work preempted by self-preempt force."""
    reason = str(manage_result.get("reason") or "")
    active = manage_result.get("active_work")
    if isinstance(active, dict):
        busy = active.get("busy_reasons")
        if isinstance(busy, list) and busy:
            return str(busy[0])
    for marker in _SELF_PREEMPT_MARKERS:
        if marker in reason.lower():
            return marker
    return reason or "in-flight work"


def restart_intent_persisted(manage_result: dict[str, Any]) -> bool:
    """True when manage deferred with a durable restart intent that will be consumed."""
    return bool(manage_result.get("restart_intent_id"))


def deferred_is_self_preemptable(
    service: str, manage_result: dict[str, Any]
) -> bool:
    """True when a busy deferral is the commissioning seat's own CSE/MCP heat.

    Durable drain intents (GIW-style) are not self-preempt — those stay queued.
    """
    if service not in _FORCE_ALLOWED_SERVICES:
        return False
    if restart_intent_persisted(manage_result):
        return False
    blob = str(manage_result).lower()
    return any(marker in blob for marker in _SELF_PREEMPT_MARKERS)


def execution_for_manage_deferred(
    row: PropagationRow,
    *,
    row_id: str,
    manage_result: dict[str, Any],
) -> dict[str, Any]:
    """Map a manage ``status=deferred`` outcome to a truthful row execution dict."""
    reason = str(
        manage_result.get("reason")
        or manage_result.get("state")
        or "manage_deferred"
    )
    if restart_intent_persisted(manage_result):
        set_defer_reason(row_id, DEFER_MANAGE_QUEUED_DRAIN)
        return {
            "service": row.service,
            "row_id": row_id,
            "status": "queued",
            "reason": reason,
            "manage": manage_result,
            "next": (
                "manage drain queue will fire sync_restart — poll liveness for "
                "code_version and process identity change"
            ),
        }
    mark_harvest_wanted(row_id)
    return {
        "service": row.service,
        "row_id": row_id,
        "status": "harvest_wanted",
        "reason": reason,
        "manage": manage_result,
        "next": (
            "manage busy deferral with no restart_intent — harvest_wanted marker "
            "persisted; charter tick will consume at next between-window pass"
        ),
    }


async def run_propagation_in_seat(
    job: AutoJob,
    *,
    client: CursorBusClient,
    queue: Any,
    model: dict[str, Any],
    effort: dict[str, Any],
    gate_plan: dict[str, Any],
) -> dict[str, Any]:
    """Mint propagation ledger rows and hand restarts to manage (drain-queued)."""
    admission = admit_propagate_body(job.body)
    if not admission.approved:
        error = admission.error or {"reason": "propagate_admission_missing"}
        summary = str(error.get("summary", "propagate admission failed"))
        return await post_terminal_status(
            job,
            client=client,
            queue=queue,
            summary=summary,
            disposition="blocked",
            contract=PROPAGATE_CONTRACT,
            terminal_status="status:blocked",
            payload={"summary": summary, **error},
            failed=True,
        )

    stamped = [
        row.model_copy(
            update={
                "mint_thread": str(job.thread_id),
                "mint_turn": job.turn_number,
                "reason": row.reason or "operator restart request via cursor-auto",
            }
        )
        for row in admission.rows
    ]
    row_ids = upsert_open_rows(list(stamped))
    executions: list[dict[str, Any]] = []
    for row, row_id in zip(stamped, row_ids, strict=True):
        executions.append(
            await _execute_row(row, row_id=row_id, from_agent=job.from_agent)
        )

    disposition = _disposition_for(executions)
    summary = _summary_for(disposition, executions)
    escalations = _self_preempt_escalations_for(executions)
    payload: dict[str, Any] = {
        "summary": summary,
        "disposition": disposition,
        "propagation": [row.model_dump() for row in stamped],
        "row_ids": row_ids,
        "executions": executions,
        "flags": list(admission.flags),
        "requested_model": model["requested"],
        "requested_effort": effort["requested"],
        "gate_plan": gate_plan,
        "request_turn": job.turn_number,
    }
    if escalations:
        payload["self_preempt_escalations"] = escalations
    return await post_terminal_status(
        job,
        client=client,
        queue=queue,
        summary=summary,
        disposition=disposition,
        contract=PROPAGATE_CONTRACT,
        payload=payload,
    )


async def _execute_row(
    row: PropagationRow,
    *,
    row_id: str,
    from_agent: str = "",
) -> dict[str, Any]:
    from scripts.model_manager.ui.controller.charter_runner.propagation_execute import (
        dispatch_proof_probe,
    )

    # Always hand to manage — it drain-queues when busy. Do not I2-short-circuit
    # as "scheduled/parked" without a manage call (operator bind 2026-07-30).
    dispatch_before = await asyncio.to_thread(dispatch_proof_probe, row)
    if dispatch_before.error is not None:
        fail_row(
            row_id,
            proof_payload={
                "proof_class_requested": dispatch_before.proof_class_requested,
                "proof_class_executed": dispatch_before.proof_class_executed,
            },
            reason=dispatch_before.error,
        )
        return {
            "service": row.service,
            "row_id": row_id,
            "status": "failed",
            "reason": dispatch_before.error,
            "proof_class_requested": dispatch_before.proof_class_requested,
            "proof_class_executed": dispatch_before.proof_class_executed,
        }
    before = dispatch_before.payload
    force = bool(row.force)
    if force and row.service not in _FORCE_ALLOWED_SERVICES:
        fail_row(
            row_id,
            proof_payload={
                "proof_class_requested": dispatch_before.proof_class_requested,
                "proof_class_executed": dispatch_before.proof_class_executed,
            },
            reason="force_only_allowed_for_mcp_or_cdp_ask",
        )
        return {
            "service": row.service,
            "row_id": row_id,
            "status": "failed",
            "reason": "force_only_allowed_for_mcp_or_cdp_ask",
            "proof_class_requested": dispatch_before.proof_class_requested,
            "proof_class_executed": dispatch_before.proof_class_executed,
        }
    manage_result = await asyncio.to_thread(
        sync_restart_service,
        row.service,
        reason=(
            "operator-proxy self-preempt (own cdp_ask_live)"
            if force
            else "operator propagate via cursor-auto"
        ),
        force=force,
    )
    status = str(manage_result.get("status") or "unknown")
    # Operator bind: do not harvest_wanted-pushback a self-preemptable mcp/cdp_ask
    # restart — retry once with force and advise disconnect (mcp).
    self_preempt_applied = False
    self_preempt_suppressed = False
    preempted_label: str | None = None
    deferred_preemptable = (
        status == "deferred"
        and not force
        and deferred_is_self_preemptable(row.service, manage_result)
    )
    if deferred_preemptable and not row.allow_self_preempt:
        self_preempt_suppressed = True
    elif deferred_preemptable:
        force = True
        self_preempt_applied = True
        preempted_label = _preempted_work_label(manage_result)
        manage_result = await asyncio.to_thread(
            sync_restart_service,
            row.service,
            reason=(
                "operator-proxy self-preempt auto (cursor-auto; "
                f"from_agent={from_agent or 'unknown'}; preempted={preempted_label})"
            ),
            force=True,
        )
        status = str(manage_result.get("status") or "unknown")
    if status == "deferred":
        deferred_out = execution_for_manage_deferred(
            row, row_id=row_id, manage_result=manage_result
        )
        if self_preempt_suppressed:
            deferred_out["self_preempt_suppressed"] = True
            deferred_out["would_preempt"] = _preempted_work_label(manage_result)
        return deferred_out
    if status == "error":
        set_defer_reason(row_id, str(manage_result.get("reason") or "manage_error"))
        return {
            "service": row.service,
            "row_id": row_id,
            "status": "failed",
            "manage": manage_result,
        }
    after_dispatch = await asyncio.to_thread(dispatch_proof_probe, row)
    if after_dispatch.error is not None:
        fail_row(
            row_id,
            proof_payload={
                "proof_class_requested": after_dispatch.proof_class_requested,
                "proof_class_executed": after_dispatch.proof_class_executed,
            },
            reason=after_dispatch.error,
        )
        return {
            "service": row.service,
            "row_id": row_id,
            "status": "failed",
            "reason": after_dispatch.error,
            "manage": manage_result,
        }
    after = after_dispatch.payload
    advisory = (
        MCP_DISCONNECT_ADVISORY
        if force and row.service == "mcp"
        else None
    )
    if proof_observed(row, after, before=before):
        close_row(
            row_id,
            proof_payload={
                **(after or {}),
                "proof_class_requested": after_dispatch.proof_class_requested,
                "proof_class_executed": after_dispatch.proof_class_executed,
            },
        )
        out: dict[str, Any] = {
            "service": row.service,
            "row_id": row_id,
            "status": "executed",
            "manage": manage_result,
            "proof": after,
            "proof_before": before,
            "proof_class_requested": after_dispatch.proof_class_requested,
            "proof_class_executed": after_dispatch.proof_class_executed,
            "force": force,
            "self_preempt_applied": self_preempt_applied,
        }
        if self_preempt_applied and preempted_label:
            out["preempted"] = preempted_label
        if advisory:
            out["advisory"] = advisory
        return out
    set_defer_reason(row_id, "proof_pending")
    out = {
        "service": row.service,
        "row_id": row_id,
        "status": "submitted",
        "manage": manage_result,
        "proof": after,
        "proof_before": before,
        "force": force,
        "self_preempt_applied": self_preempt_applied,
    }
    if self_preempt_applied and preempted_label:
        out["preempted"] = preempted_label
    if advisory:
        out["advisory"] = advisory
    return out


# Weakest per-row status floors the envelope disposition (a:27414 derive-from-executions).
_ROW_RANK: dict[str, int] = {
    "failed": 0,
    "blocked": 1,
    "harvest_wanted": 2,
    "queued": 2,
    "submitted": 3,
    "executed": 4,
}
_ENVELOPE_FROM_ROW: dict[str, str] = {
    "failed": "failed",
    "blocked": "blocked",
    "harvest_wanted": "harvest_wanted",
    "queued": "queued",
    "submitted": "submitted",
    "executed": "propagated",
}


def _row_status(item: dict[str, Any]) -> str:
    """Effective per-row status for envelope floor derivation."""
    manage = item.get("manage")
    if isinstance(manage, dict) and str(manage.get("status") or "") == "error":
        return "failed"
    status = str(item.get("status") or "")
    if status in _ROW_RANK:
        return status
    return "failed"


def _failure_reason(item: dict[str, Any]) -> str:
    manage = item.get("manage")
    if isinstance(manage, dict):
        for key in ("reason", "error", "message"):
            value = manage.get(key)
            if value:
                return str(value)
    reason = item.get("reason")
    if reason:
        return str(reason)
    return "unknown"


def _format_failed_services(executions: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"{str(item.get('service') or '?')} (reason={_failure_reason(item)})"
        for item in executions
    )


def _disposition_for(executions: list[dict[str, Any]]) -> str:
    """Derive envelope disposition purely from executions[] — weakest row floors."""
    if not executions:
        return "failed"
    row_statuses = [_row_status(item) for item in executions]
    weakest = min(row_statuses, key=lambda status: _ROW_RANK[status])
    if len(set(row_statuses)) == 1:
        return _ENVELOPE_FROM_ROW[weakest]
    if weakest == "failed":
        return "failed"
    return _ENVELOPE_FROM_ROW[weakest]


def _self_preempt_escalations_for(
    executions: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Surface self-preempt force escalations at the closeout envelope layer."""
    escalations: list[dict[str, str]] = []
    for item in executions:
        if not item.get("self_preempt_applied"):
            continue
        escalations.append(
            {
                "service": str(item.get("service") or "?"),
                "preempted": str(
                    item.get("preempted")
                    or _preempted_work_label(item.get("manage") or {})
                ),
                "force": "true",
            }
        )
    return escalations


def _summary_for(disposition: str, executions: list[dict[str, Any]]) -> str:
    services = ", ".join(str(item.get("service") or "?") for item in executions)
    advisories = [
        str(item["advisory"])
        for item in executions
        if item.get("advisory")
    ]
    advisory_suffix = f" {advisories[0]}" if advisories else ""
    if disposition in {"executed", "propagated"}:
        preempt_items = [
            item for item in executions if item.get("self_preempt_applied")
        ]
        base = (
            f"Auto propagated restart for {services}; proof-of-live observed."
            if disposition == "propagated"
            else f"Auto executed propagation restart for {services}; proof-of-live observed."
        )
        if preempt_items:
            preempt_detail = "; ".join(
                f"{item.get('service') or '?'} preempted "
                f"{item.get('preempted') or _preempted_work_label(item.get('manage') or {})}"
                for item in preempt_items
            )
            base = (
                f"Auto propagated restart for {services} via self-preempt force "
                f"({preempt_detail}); proof-of-live observed."
                if disposition == "propagated"
                else (
                    f"Auto executed propagation restart for {services} via "
                    f"self-preempt force ({preempt_detail}); proof-of-live observed."
                )
            )
        return base + advisory_suffix
    if disposition == "queued":
        reasons = ", ".join(str(item.get("reason") or "?") for item in executions)
        return (
            f"Auto queued propagation restart for {services} on manage drain "
            f"(reason={reasons}). Restart will fire after drain — not ledger-only. "
            "Live only when code_ref ancestry is satisfied and process identity changed."
        )
    if disposition == "harvest_wanted":
        reasons = ", ".join(str(item.get("reason") or "?") for item in executions)
        suppressed = [
            item
            for item in executions
            if item.get("self_preempt_suppressed")
        ]
        base = (
            f"Auto propagation harvest_wanted for {services} (reason={reasons}). "
            "Open ledger row marked — charter tick will consume at between-window pass."
        )
        if suppressed:
            veto_detail = "; ".join(
                f"{item.get('service') or '?'} (would_preempt="
                f"{item.get('would_preempt') or 'in-flight work'})"
                for item in suppressed
            )
            base = (
                f"Auto propagation harvest_wanted for {services} — "
                f"self-preempt vetoed ({veto_detail}); reason={reasons}. "
                "Open ledger row marked — charter tick will consume at between-window pass."
            )
        return base
    if disposition == "blocked":
        reasons = ", ".join(str(item.get("reason") or "?") for item in executions)
        return (
            f"Auto propagation restart blocked for {services} — manage busy deferral "
            f"with no drain queue (reason={reasons}). Nothing will fire automatically."
        )
    if disposition == "failed":
        if not executions:
            return "Auto propagation restart failed: no services were executed."
        failed = [item for item in executions if _row_status(item) == "failed"]
        if failed and len(failed) < len(executions):
            ok_services = ", ".join(
                str(item.get("service") or "?")
                for item in executions
                if _row_status(item) != "failed"
            )
            return (
                f"Auto propagation restart failed — partial progress for "
                f"{ok_services}; failed: {_format_failed_services(failed)}."
            )
        return f"Auto propagation restart failed for {_format_failed_services(failed or executions)}."
    if disposition == "submitted":
        base = (
            f"Auto submitted propagation restart for {services} — restart handed off; "
            "ledger row open until proof closes."
        )
        return base + advisory_suffix
    return (
        f"Auto propagation restart for {services} — status={disposition}; "
        "see executions[] for per-service detail."
    )


__all__ = [
    "DEFER_HARVEST_WANTED",
    "DEFER_MANAGE_BUSY_DEFER",
    "DEFER_MANAGE_QUEUED_DRAIN",
    "MCP_DISCONNECT_ADVISORY",
    "deferred_is_self_preemptable",
    "execution_for_manage_deferred",
    "restart_intent_persisted",
    "run_propagation_in_seat",
]
