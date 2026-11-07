"""Model-ban gate for team_dispatch_admit plans."""

from __future__ import annotations

from typing import Any

from implement_admission.model_ban import (
    evaluate_dispatch_model_ban,
    model_ban_wire_fields,
)
from implement_admission.model_ban_admit import ban_override_pair_reject

from ._envelope import admit_envelope


def ban_override_reject(opts: dict[str, Any]) -> dict[str, Any] | None:
    enabled, reason, _caller = model_ban_wire_fields(opts)
    return ban_override_pair_reject(enabled, reason)


def _workflow_key_for_plan(opts: dict[str, Any], plan: dict[str, Any]) -> str | None:
    workflow_key = str(plan.get("workflow") or "").strip() or None
    if workflow_key and not workflow_key.startswith(
        ("workflows.", "team-dispatch-admit.")
    ):
        workflow_key = f"workflows.{workflow_key}"
    if not workflow_key:
        job = str(plan.get("job") or opts.get("job") or "").strip().lower()
        if job == "conductor":
            workflow_key = "team-dispatch-admit.model_defaults.conductor"
        elif job:
            workflow_key = "workflows.auto_judgment"
    return workflow_key


def _append_override(plan: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(plan)
    existing = merged.get("model_ban_overrides")
    overrides: list[dict[str, Any]] = []
    if isinstance(existing, list):
        overrides.extend(dict(x) for x in existing if isinstance(x, dict))
    elif isinstance(merged.get("model_ban_override"), dict):
        overrides.append(dict(merged["model_ban_override"]))
    overrides.append(dict(payload))
    merged["model_ban_overrides"] = overrides
    merged["model_ban_override"] = dict(payload)
    return merged


def _gate_resolved_model(
    opts: dict[str, Any],
    plan: dict[str, Any],
    *,
    resolved_model: str,
    explicit_pin: bool,
    workflow_key: str | None,
) -> dict[str, Any]:
    enabled, reason, caller = model_ban_wire_fields(opts)
    role = plan.get("role") or opts.get("role")
    reject, override_payload = evaluate_dispatch_model_ban(
        resolved_model=resolved_model,
        explicit_pin=explicit_pin,
        workflow_key=workflow_key,
        role=str(role) if role is not None else None,
        ban_override=enabled,
        ban_override_reason=reason,
        caller_agent=caller,
        dispatch_id=(
            str(opts.get("dispatch_id")).strip()
            if opts.get("dispatch_id") is not None
            else None
        ),
    )
    if reject is not None:
        return reject
    if override_payload is not None:
        return _append_override(plan, override_payload)
    return plan


def apply_model_ban_to_plan(
    opts: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    if plan.get("admitted") is False:
        return plan
    workflow_key = _workflow_key_for_plan(opts, plan)
    model = plan.get("model")
    if model and str(model).strip():
        explicit_pin = bool(str(opts.get("model") or "").strip())
        plan = _gate_resolved_model(
            opts,
            plan,
            resolved_model=str(model),
            explicit_pin=explicit_pin,
            workflow_key=workflow_key,
        )
        if plan.get("admitted") is False:
            return plan
    hop = plan.get("hop_successor_default")
    if hop and str(hop).strip():
        hop_key = "team-dispatch-admit.model_defaults.conductor.hop_successor"
        plan = _gate_resolved_model(
            opts,
            plan,
            resolved_model=str(hop),
            explicit_pin=False,
            workflow_key=hop_key,
        )
    return plan


def admit_with_model_ban(opts: dict[str, Any], **admit_kwargs: Any) -> dict[str, Any]:
    plan = admit_envelope(**admit_kwargs)
    return apply_model_ban_to_plan(opts, plan)
