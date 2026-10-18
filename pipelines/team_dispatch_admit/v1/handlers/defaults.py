"""Defaults step — coercion, backend selection, model/seat resolution."""

from __future__ import annotations

import json
from typing import Any, override

from agent_seat.dispatch_role_catalog import auto_seats, generate_roles
from agent_seat.profiles import CapabilityProfile, get_profile, seat_to_family
from agent_seat.registry import normalize_agent_slug, normalize_bus_address
from cursor_capabilities import is_cursor_model_denied
from implement_admission.check_review_substrate import (
    CheckReviewAdmissionReject,
    coerce_check_review_omit_to_cursor_seat,
    evaluate_check_review_admission,
)
from implement_admission.routing import load_route_policy
from implement_admission.workflow_registry import (
    MECHANICAL_WORKFLOW,
    load_auto_judgment_default_model,
    load_workflow_registry,
)
from model_id import ModelId
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._envelope import (
    Backend,
    admit_envelope,
    is_cdp_model,
    is_cursor_substrate_model,
    pass_through_reject,
    reject_envelope,
)


def _teach(axis: str) -> dict[str, Any]:
    return {
        "expected_axis": axis,
        "valid_seats": auto_seats(),
        "valid_roles": list(generate_roles()),
    }


def _resolve_auto_seat_profile(
    seat: str,
) -> tuple[str, str, str, CapabilityProfile]:
    canonical = normalize_agent_slug(seat)
    if canonical not in auto_seats():
        raise ValueError("seat_unknown")
    if canonical == "cursor-sdk":
        return "cursor-sdk", "cursor", "sdk", get_profile("cursor", "sdk")
    family = seat_to_family(canonical)
    parts = canonical.split("-", 1)
    if family is None or len(parts) != 2:
        raise ValueError("seat_unknown")
    platform = parts[1]
    profile = get_profile(family, platform)
    if not profile.auto_dispatchable:
        raise ValueError("seat_not_auto_dispatchable")
    return normalize_bus_address(f"{family}-{platform}"), family, platform, profile


def _is_sdk_substrate(profile: CapabilityProfile) -> bool:
    return (
        profile.family == "cursor"
        and profile.platform == "sdk"
        and profile.auto_dispatchable
    )


def _sdk_admission(
    *,
    role: str | None,
    seat: str | None,
    model: str | None,
) -> bool:
    if role is not None and normalize_agent_slug(role) == "cursor-sdk":
        return False
    if seat is not None and str(seat).strip():
        try:
            _resolve_auto_seat_profile(str(seat))
        except ValueError:
            return False
        return True
    return is_cursor_substrate_model(model)


def _select_backend(
    *,
    role: str | None,
    seat: str | None,
    model: str | None,
) -> Backend:
    if is_cdp_model(model):
        return "cdp"
    if role is not None and normalize_agent_slug(role) == "cursor-sdk":
        pass
    elif seat is not None and str(seat).strip() and _sdk_admission(
        role=None, seat=seat, model=None
    ):
        return "cursor_sdk"
    elif is_cursor_substrate_model(model):
        return "cursor_sdk"
    elif role is not None and str(role).strip():
        return "api_role"
    return "frontier"


def _omit_model(*, job: str | None) -> tuple[str, str]:
    registry = load_workflow_registry()
    key = (job or "").strip().lower()
    if key in {"implement", "mechanical"}:
        binding = registry.workflows.get(MECHANICAL_WORKFLOW)
        if binding is None or not binding.model:
            raise ValueError("sdk_generate_model_invalid")
        return binding.model, MECHANICAL_WORKFLOW
    wf = registry.workflow_for_contract(key) if key else None
    if wf is not None:
        return wf.model, wf.slug
    policy = load_route_policy()
    return load_auto_judgment_default_model(policy), "auto_judgment"


def _resolve_sdk_target(
    seat: str,
    *,
    model: str | None,
    job: str | None,
) -> tuple[str, str, str, str, str]:
    to_agent, family, platform, profile = _resolve_auto_seat_profile(seat)
    if profile.manual_handoff:
        raise ValueError("seat_not_auto_dispatchable")
    if not _is_sdk_substrate(profile):
        raise ValueError("sdk_substrate_required")
    resolved = model or _omit_model(job=job)[0]
    if resolved == "auto":
        resolved = "cursor/auto"
    if not resolved:
        raise ValueError("sdk_generate_model_invalid")
    if not resolved.startswith("cursor/"):
        raise ValueError("sdk_generate_model_invalid")
    if profile.family == "cursor" and profile.platform == "sdk":
        if is_cursor_model_denied(resolved):
            raise ValueError("sdk_generate_model_invalid")
    elif profile.allowed_models and resolved not in profile.allowed_models:
        raise ValueError("sdk_generate_model_invalid")
    workflow = _omit_model(job=job)[1] if not model else _omit_model(job=job)[1]
    return to_agent, family, platform, resolved, workflow


def _seat_field_error(exc: ValueError) -> dict[str, Any]:
    code = str(exc)
    if code == "seat_unknown":
        return reject_envelope(
            status_code=422,
            code="seat_unknown",
            field="seat",
            reason="Unknown auto-dispatch seat",
            details=_teach("seat"),
        )
    if code == "seat_not_auto_dispatchable":
        return reject_envelope(
            status_code=422,
            code="seat_not_auto_dispatchable",
            field="seat",
            reason="seat is not auto-dispatchable",
            details=_teach("seat"),
        )
    if code == "sdk_substrate_required":
        return reject_envelope(
            status_code=422,
            code="sdk_substrate_required",
            field="seat",
            reason="SDK substrate required",
        )
    return reject_envelope(
        status_code=422,
        code="sdk_generate_model_invalid",
        field="model",
        reason="invalid sdk model",
    )


def evaluate_defaults(
    opts: dict[str, Any], validate_out: dict[str, Any]
) -> dict[str, Any]:
    prior = pass_through_reject(validate_out)
    if prior.get("admitted") is False:
        return prior

    op = str(opts.get("op") or "")
    role = opts.get("role")
    seat = opts.get("seat")
    model = opts.get("model")
    job = opts.get("job")
    dispatch_id = opts.get("dispatch_id")

    if op == "to_thread":
        eff_role = role if role is not None and str(role).strip() else "synthesizer"
        return admit_envelope(
            backend="frontier",
            role=eff_role,
            seat=seat,
            model=model,
            job=job,
            workflow=None,
        )

    role, seat, model, _coerced = coerce_check_review_omit_to_cursor_seat(
        role, seat, model
    )

    sdk_admit = _sdk_admission(role=role, seat=seat, model=model)
    if dispatch_id and not sdk_admit:
        return reject_envelope(
            status_code=422,
            code="dispatch_id_sdk_only",
            field="dispatch_id",
            reason=(
                "dispatch_id is only honored on seat='cursor-sdk' generate; "
                "other branches mint their own ids"
            ),
        )

    backend = _select_backend(role=role, seat=seat, model=model)

    if backend == "cdp":
        seat_norm = (seat or "").strip().lower()
        if seat_norm in {"cursor-sdk", "cursor_sdk"}:
            return reject_envelope(
                status_code=422,
                code="cdp_cursor_sdk_seat_rejected",
                field="model",
                reason=(
                    "model=cdp/… selects the web-anthropic-cdp substrate and cannot "
                    "combine with seat=cursor-sdk"
                ),
            )
        if role and model:
            try:
                btype = ModelId.parse(model).backend_type
            except (TypeError, ValueError):
                btype = None
            if btype in {"cdp", "cursor_sdk"}:
                return reject_envelope(
                    status_code=422,
                    code="substrate_model_role_conflict",
                    field="role",
                    reason=(
                        f"role={role!r} cannot combine with substrate model={model!r}; "
                        "omit role (model prefix selects transport) or use a cloud model"
                    ),
                )
        return admit_envelope(
            backend="cdp",
            role=role,
            seat=seat,
            model=model,
            job=job,
            workflow=None,
        )

    has_role = bool(role and str(role).strip())
    has_seat = bool(seat and str(seat).strip())
    if not has_role and not has_seat and not is_cursor_substrate_model(model):
        return reject_envelope(
            status_code=422,
            code="role_or_seat_required",
            field="role",
            reason="generate requires exactly one of role or seat",
            details=_teach("role|seat"),
        )
    if has_role and has_seat:
        return reject_envelope(
            status_code=422,
            code="role_seat_exclusive",
            field="role",
            reason="role and seat are mutually exclusive on generate/to_thread",
            details=_teach("role|seat"),
        )
    if has_role and normalize_agent_slug(str(role)) == "cursor-sdk":
        return reject_envelope(
            status_code=422,
            code="role_is_not_a_seat",
            field="role",
            reason=(
                "'cursor-sdk' names an executor seat (platform=sdk), not a functional role. "
                'Use seat="cursor-sdk".'
            ),
            details=_teach("seat"),
        )

    if backend == "cursor_sdk" and has_role and model:
        try:
            btype = ModelId.parse(model).backend_type
        except (TypeError, ValueError):
            btype = None
        if btype in {"cdp", "cursor_sdk"}:
            return reject_envelope(
                status_code=422,
                code="substrate_model_role_conflict",
                field="role",
                reason=(
                    f"role={role!r} cannot combine with substrate model={model!r}; "
                    "omit role (model prefix selects transport) or use a cloud model"
                ),
            )

    eff_seat = seat or ("cursor-sdk" if backend == "cursor_sdk" else None)
    if eff_seat and str(eff_seat).strip():
        try:
            _resolve_auto_seat_profile(str(eff_seat))
        except ValueError as exc:
            err = _seat_field_error(exc)
            if err.get("code") == "seat_unknown":
                err["reason"] = f"Unknown auto-dispatch seat {eff_seat!r}"
            return err

    if backend == "cursor_sdk":
        try:
            to_agent, family, platform, resolved, workflow = _resolve_sdk_target(
                str(eff_seat or "cursor-sdk"),
                model=model,
                job=job,
            )
        except ValueError as exc:
            return _seat_field_error(exc)
        if role:
            verdict = evaluate_check_review_admission(
                str(role),
                resolved,
                api_role_with_cursor_on_api_profile=False,
            )
            if isinstance(verdict, CheckReviewAdmissionReject):
                return reject_envelope(
                    status_code=422,
                    code=verdict.code,
                    field=verdict.field,
                    reason=verdict.reason,
                )
        return admit_envelope(
            backend="cursor_sdk",
            role=role,
            seat=eff_seat or "cursor-sdk",
            model=resolved,
            job=job,
            workflow=workflow,
            to_agent=to_agent,
            family=family,
            platform=platform,
        )

    if backend == "api_role" and role:
        if model:
            verdict = evaluate_check_review_admission(
                str(role),
                model,
                api_role_with_cursor_on_api_profile=True,
            )
            if isinstance(verdict, CheckReviewAdmissionReject):
                return reject_envelope(
                    status_code=422,
                    code=verdict.code,
                    field=verdict.field,
                    reason=verdict.reason,
                )
        return admit_envelope(
            backend="api_role",
            role=role,
            seat=seat,
            model=model,
            job=job,
            workflow=None,
        )

    return admit_envelope(
        backend="frontier",
        role=role,
        seat=seat,
        model=model,
        job=job,
        workflow=None,
    )


class TeamDispatchAdmitDefaultsHandler(BaseHandler):
    step_type = "team_dispatch_admit_defaults_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = dict(getattr(context, "options", {}) or {})
        validate_out = _step_json(context, "validate")
        payload = evaluate_defaults(opts, validate_out)
        return StepOutput(raw=json.dumps(payload), json=payload)


def _step_json(context: Any, name: str) -> dict[str, Any]:
    outputs = getattr(context, "outputs", {}) or {}
    step_out = outputs.get(name)
    json_out = getattr(step_out, "json", None) if step_out is not None else None
    return json_out if isinstance(json_out, dict) else {}
