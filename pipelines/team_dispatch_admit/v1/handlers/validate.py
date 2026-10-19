"""Validate step — raw-body wrap refusal only."""

from __future__ import annotations

import json
from typing import Any, override

from agent_seat.dispatch_role_catalog import auto_seats, generate_roles
from agent_seat.registry import normalize_agent_slug
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._envelope import is_cursor_substrate_model, reject_envelope


def _generate_teaching_data(expected_axis: str) -> dict[str, Any]:
    return {
        "expected_axis": expected_axis,
        "valid_seats": auto_seats(),
        "valid_roles": generate_roles(),
    }


def _sdk_admission_raw(
    *,
    role: str | None,
    seat: str | None,
    model: str | None,
) -> bool:
    if role is not None and normalize_agent_slug(role) == "cursor-sdk":
        return False
    if seat is not None and str(seat).strip():
        from agent_seat.profiles import get_profile, seat_to_family
        from agent_seat.registry import normalize_bus_address

        canonical = normalize_agent_slug(seat)
        if canonical not in auto_seats():
            return False
        if canonical == "cursor-sdk":
            get_profile("cursor", "sdk")
            return True
        family = seat_to_family(canonical)
        parts = canonical.split("-", 1)
        if family is None or len(parts) != 2:
            return False
        profile = get_profile(family, parts[1])
        if not profile.auto_dispatchable:
            return False
        normalize_bus_address(f"{family}-{parts[1]}")
        return True
    return is_cursor_substrate_model(model)


def evaluate_validate(opts: dict[str, Any]) -> dict[str, Any]:
    op = str(opts.get("op") or "")
    if op != "generate":
        return {"admitted": True, "phase": "validate", "skipped": True}

    role = opts.get("role")
    seat = opts.get("seat")
    model = opts.get("model")
    job = opts.get("job")

    if (
        str(job or "").strip().lower() == "wrap"
        and isinstance(role, str)
        and role.strip()
        and normalize_agent_slug(role) != "cursor-sdk"
        and not _sdk_admission_raw(role=role, seat=seat, model=model)
    ):
        return reject_envelope(
            status_code=422,
            code="wrap_role_not_admitted",
            field="role",
            reason=(
                "contract=wrap is only admitted on the cursor-sdk generate branch"
            ),
        )
    return {"admitted": True, "phase": "validate"}


class TeamDispatchAdmitValidateHandler(BaseHandler):
    step_type = "team_dispatch_admit_validate_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = dict(getattr(context, "options", {}) or {})
        payload = evaluate_validate(opts)
        return StepOutput(raw=json.dumps(payload), json=payload)
