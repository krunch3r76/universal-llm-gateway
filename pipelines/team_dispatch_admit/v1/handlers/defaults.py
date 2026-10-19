"""Defaults step — coercion, backend selection, model/seat resolution."""

from __future__ import annotations

import json
from typing import Any, override

from agent_seat.registry import normalize_agent_slug
from implement_admission.check_review_substrate import (
    CheckReviewAdmissionReject,
    coerce_check_review_omit_to_cursor_seat,
    evaluate_check_review_admission,
    is_check_review_api_role,
    load_check_review_default_model,
)
from implement_admission.routing import load_route_policy
from implement_admission.workflow_registry import CHECK_REVIEW_WORKFLOW
from model_id import ModelId
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._envelope import (
    admit_envelope,
    is_cursor_substrate_model,
    pass_through_reject,
    reject_envelope,
)
from .defaults_split import (
    _resolve_auto_seat_profile,
    _resolve_sdk_target,
    _sdk_admission,
    _seat_field_error,
    _select_backend,
    _teach,
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

    check_review_workflow: str | None = None
    if (
        role
        and is_check_review_api_role(str(role))
        and not (seat and str(seat).strip())
        and not model
    ):
        model = load_check_review_default_model(load_route_policy())
        role, seat = None, "cursor-sdk"
        check_review_workflow = CHECK_REVIEW_WORKFLOW
    else:
        role, seat, model, coerced = coerce_check_review_omit_to_cursor_seat(
            role, seat, model
        )
        if coerced:
            check_review_workflow = CHECK_REVIEW_WORKFLOW

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
        verdict = evaluate_check_review_admission(
            str(role),
            model,
            api_role_with_cursor_on_api_profile=False,
        )
        if isinstance(verdict, CheckReviewAdmissionReject):
            return reject_envelope(
                status_code=422,
                code=verdict.code,
                field=verdict.field,
                reason=verdict.reason,
            )
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
                workflow_override=check_review_workflow,
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
