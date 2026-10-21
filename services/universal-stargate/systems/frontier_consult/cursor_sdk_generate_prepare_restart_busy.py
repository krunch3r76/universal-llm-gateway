"""L16 — restart-gate busy check scoped by ``work_key`` at sdk generate prepare.

Existing restart / drain hold sites behind fleet ``#934`` (documented for L16 wiring):

1. **Stargate CDP Gate-2 / review path** — ``dispatch_cdp_generate`` calls
   ``refuse_second_external_gate_at_fire`` in ``cdp_generate.py`` (operator-proxy /
   CDP ask occupancy from ``16416#934``).
2. **GIW lane-scoped pending-intent queue** — ``queue_kw_for_pending_intent`` in
   ``scripts/model_manager/ui/controller/intent_admit_fence.py``, invoked from
   ``services/git_integration_worker/routes/cursor_sdk.py`` (pending-drain fence).
"""

from __future__ import annotations

import asyncio
from typing import Any

from .admission import FrontierEndpointError
from .admit_provenance import AdmitProvenanceContext

MAESTRO_RESTART_SERVICES = ("stargate", "git_integration_worker")


def normalize_admit_work_key(
    work_key: str | None,
    admit_provenance: AdmitProvenanceContext | None,
) -> str | None:
    if admit_provenance is not None and admit_provenance.work_key:
        raw = str(admit_provenance.work_key).strip()
    else:
        raw = str(work_key or "").strip()
    if not raw:
        return None
    return raw.lower()


def busy_cursor_sdk_work_keys(active_work: dict[str, Any]) -> frozenset[str]:
    """``work_key`` values on live cursor-sdk rows in a GIW active-work snapshot."""
    keys: set[str] = set()
    for op in active_work.get("active_ops") or []:
        if not isinstance(op, dict):
            continue
        kind = str(op.get("kind") or "").strip().lower()
        route = str(op.get("route") or "")
        if kind != "cursor_sdk" and route != "/api/v1/cursor/dispatch":
            continue
        wk = str(op.get("work_key") or "").strip().lower()
        if wk:
            keys.add(wk)
    return frozenset(keys)


def restart_gate_pending(*, intent_status: str | None) -> bool:
    from scripts.model_manager.ui.controller.restart_intent_states import (
        STATUS_PENDING_DRAIN,
    )

    return intent_status == STATUS_PENDING_DRAIN


def _nested_or_resumed_child(
    *,
    nest_under: str | None,
    caller_dispatch_id: str | None,
    resume_of: str | None,
) -> bool:
    fields = (nest_under, caller_dispatch_id, resume_of)
    return any(str(x or "").strip() for x in fields)


def restart_busy_should_defer_admit(
    *,
    admit_work_key: str | None,
    busy_work_keys: frozenset[str],
    restart_gate_active: bool,
    nest_under: str | None = None,
    caller_dispatch_id: str | None = None,
    resume_of: str | None = None,
) -> bool:
    """True when prepare defers once (no poll) for restart-drain ∩ active generate."""
    if not restart_gate_active or not busy_work_keys:
        return False
    if _nested_or_resumed_child(
        nest_under=nest_under,
        caller_dispatch_id=caller_dispatch_id,
        resume_of=resume_of,
    ):
        return False
    if admit_work_key is None:
        return False
    return admit_work_key in busy_work_keys


def _any_restart_gate_pending(
    loader: Any | None = None,
) -> bool:
    from scripts.model_manager.ui.controller.intent_admit_fence import (
        pending_service_intent_status,
    )

    for service in MAESTRO_RESTART_SERVICES:
        status = pending_service_intent_status(service, loader=loader)
        if restart_gate_pending(intent_status=status):
            return True
    return False


def raise_if_restart_busy_blocks_generate(
    *,
    request_id: str,
    admit_work_key: str | None,
    busy_work_keys: frozenset[str],
    restart_gate_active: bool,
    nest_under: str | None = None,
    caller_dispatch_id: str | None = None,
    resume_of: str | None = None,
) -> None:
    if not restart_busy_should_defer_admit(
        admit_work_key=admit_work_key,
        busy_work_keys=busy_work_keys,
        restart_gate_active=restart_gate_active,
        nest_under=nest_under,
        caller_dispatch_id=caller_dispatch_id,
        resume_of=resume_of,
    ):
        return
    blocking = sorted(busy_work_keys)
    raise FrontierEndpointError(
        request_id=request_id,
        field="work_key",
        reason=(
            "cursor-sdk generate deferred while restart drain holds active "
            "generate work for this work_key"
        ),
        status_code=503,
        code="restart_busy_deferred",
        details={
            "admit_work_key": admit_work_key,
            "busy_work_keys": blocking,
            "retryable": True,
            "fix_hint": (
                "Wait for the in-flight generate on this work_key to finish, "
                "or admit an independent act with a different work_key."
            ),
        },
    )


async def enforce_restart_busy_scoped_by_work_key(
    *,
    request_id: str,
    work_key: str | None,
    admit_provenance: AdmitProvenanceContext | None,
    active_work_loader: Any | None = None,
    intent_loader: Any | None = None,
    nest_under: str | None = None,
    caller_dispatch_id: str | None = None,
    resume_of: str | None = None,
) -> None:
    """Consult restart intent + GIW active-work; defer only on matching ``work_key``.

    When the GIW active-work probe returns a non-dict payload (endpoint down or
    malformed), this gate **fail-opens** at Stargate prepare so a transient probe
    error does not wedge independent admissions during drain.
    """
    if not _any_restart_gate_pending(loader=intent_loader):
        return

    if active_work_loader is not None:
        payload = active_work_loader()
    else:
        from bus_watch.roster import fetch_giw_active_work_payload

        payload = await asyncio.to_thread(fetch_giw_active_work_payload)
    if not isinstance(payload, dict):
        return

    admit_key = normalize_admit_work_key(work_key, admit_provenance)
    busy_keys = busy_cursor_sdk_work_keys(payload)
    raise_if_restart_busy_blocks_generate(
        request_id=request_id,
        admit_work_key=admit_key,
        busy_work_keys=busy_keys,
        restart_gate_active=True,
        nest_under=nest_under,
        caller_dispatch_id=caller_dispatch_id,
        resume_of=resume_of,
    )
