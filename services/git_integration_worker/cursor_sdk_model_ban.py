"""Model-ban gate for cursor-sdk worker dispatch admission.

Primary entry: Stargate ``cursor_sdk_worker_dispatch`` (team_dispatch generate).
Also relayed via Stargate ``POST /api/v1/providers/cursor/dispatch`` — waiver ids are
persisted on the dispatch ledger row for audit; cross-check against the event bus is
ticketed (agent-bus:9956).
"""

from __future__ import annotations

from typing import Any

from implement_admission.model_ban import (
    ModelBanVerdict,
    check_model_ban_safe,
    model_ban_wire_fields,
)
from implement_admission.model_ban_load import (
    find_matching_ban,
    normalize_model_for_ban,
)

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest


def _parent_resolved_model(parent_dispatch_id: str) -> str | None:
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT resolved_model, record_json FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            (parent_dispatch_id,),
        ).fetchone()
    if row is None:
        return None
    resolved = str(row["resolved_model"] or "").strip()
    if resolved:
        return resolved
    try:
        import json

        record = json.loads(row["record_json"] or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    model = str(record.get("model") or "").strip()
    return model or None


def models_for_ban_check(req: CursorDispatchRequest) -> tuple[str, ...]:
    """Models that must pass the ban gate (resume checks parent + child wire)."""
    ordered: list[str] = []
    wire = str(req.model or "").strip()
    if wire:
        ordered.append(wire)
    if req.resume_of:
        parent = _parent_resolved_model(str(req.resume_of).strip())
        if parent and parent not in ordered:
            ordered.append(parent)
    return tuple(ordered)


def effective_model_for_ban_check(req: CursorDispatchRequest) -> str:
    """Primary model label for logging (first in ``models_for_ban_check``)."""
    models = models_for_ban_check(req)
    return models[0] if models else str(req.model)


def cursor_sdk_allowed_models_hint() -> list[str]:
    from cursor_capabilities import cursor_sdk_model_set_details

    details = cursor_sdk_model_set_details(explicit_pin=True)
    allowed = [str(m) for m in (details.get("allowed_models") or [])]
    return allowed


def cursor_sdk_model_set_extra_data() -> dict[str, Any]:
    from cursor_capabilities import cursor_sdk_model_set_fix_hint

    return {
        "fix_hint": cursor_sdk_model_set_fix_hint(explicit_pin=True),
        "allowed_models": cursor_sdk_allowed_models_hint(),
        "model_bans_note": (
            "cursor/grok-4.7 remains in the sdk admitted set for explicit pins only; "
            "dispatch admit bans it unless team_dispatch ban_override is echoed with "
            "model_ban_override_event_id on this request."
        ),
    }


def _verdict_for_model(
    req: CursorDispatchRequest,
    *,
    resolved_model: str,
    workflow: str,
    enabled: bool,
    reason: str | None,
    caller: str | None,
) -> ModelBanVerdict:
    matched = find_matching_ban(resolved_model)
    if matched is not None and not enabled:
        return check_model_ban_safe(
            resolved_model=resolved_model,
            explicit_pin=True,
            workflow_key=workflow,
            ban_override=False,
            caller_agent=caller,
            dispatch_id=req.dispatch_id,
        )
    if matched is not None and enabled:
        event_id = str(req.model_ban_override_event_id or "").strip()
        audit_reason = str(reason or "").strip()
        if not audit_reason or not event_id:
            return ModelBanVerdict(
                allowed=False,
                reject_envelope={
                    "admitted": False,
                    "status_code": 422,
                    "code": "model_ban_waiver_incomplete",
                    "field": "ban_override",
                    "reason": (
                        "banned model requires ban_override, "
                        "ban_override_reason, and model_ban_override_event_id"
                    ),
                },
                override_payload=None,
                matched=matched,
            )
    return check_model_ban_safe(
        resolved_model=resolved_model,
        explicit_pin=True,
        workflow_key=workflow,
        ban_override=enabled,
        ban_override_reason=reason,
        caller_agent=caller,
        dispatch_id=req.dispatch_id,
    )


def evaluate_cursor_dispatch_model_ban(
    req: CursorDispatchRequest,
) -> tuple[ModelBanVerdict, str]:
    enabled, reason, caller = model_ban_wire_fields(
        {
            "ban_override": req.ban_override,
            "ban_override_reason": req.ban_override_reason,
            "caller_agent": req.caller_agent,
        }
    )
    workflow = (
        "git_integration_worker.resume_of" if req.resume_of else "cursor_sdk.dispatch"
    )
    models = models_for_ban_check(req)
    label = effective_model_for_ban_check(req)
    for resolved_model in models:
        verdict = _verdict_for_model(
            req,
            resolved_model=resolved_model,
            workflow=workflow,
            enabled=enabled,
            reason=reason,
            caller=caller,
        )
        if not verdict.allowed:
            return verdict, resolved_model
    if not models:
        verdict = _verdict_for_model(
            req,
            resolved_model=str(req.model),
            workflow=workflow,
            enabled=enabled,
            reason=reason,
            caller=caller,
        )
        return verdict, str(req.model)
    return (
        _verdict_for_model(
            req,
            resolved_model=models[-1],
            workflow=workflow,
            enabled=enabled,
            reason=reason,
            caller=caller,
        ),
        label,
    )


def model_slugs_match(left: str, right: str) -> bool:
    try:
        return normalize_model_for_ban(left) == normalize_model_for_ban(right)
    except (TypeError, ValueError):
        return False
