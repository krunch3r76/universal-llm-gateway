"""Assemble step — final AdmitPlan (skill_rows always empty in v1)."""

from __future__ import annotations

import json
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._envelope import pass_through_reject


def evaluate_assemble(
    validate_out: dict[str, Any], defaults_out: dict[str, Any]
) -> dict[str, Any]:
    for prior in (validate_out, defaults_out):
        if prior.get("admitted") is False:
            return dict(prior)
    plan = dict(defaults_out)
    plan["skill_rows"] = []
    plan["admitted"] = True
    return plan


class TeamDispatchAdmitAssembleHandler(BaseHandler):
    step_type = "team_dispatch_admit_assemble_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        validate_out = _step_json(context, "validate")
        defaults_out = _step_json(context, "defaults")
        payload = evaluate_assemble(validate_out, defaults_out)
        return StepOutput(raw=json.dumps(payload), json=payload)


def _step_json(context: Any, name: str) -> dict[str, Any]:
    outputs = getattr(context, "outputs", {}) or {}
    step_out = outputs.get(name)
    json_out = getattr(step_out, "json", None) if step_out is not None else None
    return json_out if isinstance(json_out, dict) else {}
