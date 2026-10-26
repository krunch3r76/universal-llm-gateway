"""Assemble MechanicalLandPlan."""

from __future__ import annotations

import json
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput


def evaluate_plan(
    validate_out: dict[str, Any],
    stamp_out: dict[str, Any],
    transition_out: dict[str, Any],
    render_out: dict[str, Any],
    opts: dict[str, Any],
) -> dict[str, Any]:
    for prior in (validate_out, stamp_out):
        if prior.get("admitted") is False:
            return dict(prior)
    plan: dict[str, Any] = {
        "admitted": True,
        "phase": "plan",
        "op": validate_out.get("op"),
        "kind": validate_out.get("kind") or opts.get("kind"),
        "policy": stamp_out.get("policy"),
        "policy_sha256": stamp_out.get("policy_sha256"),
        "transition": transition_out,
    }
    if render_out.get("packet_markdown"):
        plan["packet"] = render_out
        route = transition_out.get("review_route")
        if transition_out.get("step_id") == "review" and route == "bus_request":
            plan["admit_body"] = {
                "kind": "bus_request",
                "body": render_out.get("review_request_body"),
            }
        elif transition_out.get("step_id") == "review":
            plan["admit_body"] = {
                "kind": "check_review",
                "prompt": render_out.get("check_review_prompt"),
            }
    cmd = transition_out.get("command")
    plan["admitted"] = (
        cmd not in ("block",) and validate_out.get("admitted") is not False
    )
    return plan


class MechanicalLandPlanHandler(BaseHandler):
    step_type = "mechanical_land_plan_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = dict(getattr(context, "options", {}) or {})
        payload = evaluate_plan(
            _step_json(context, "validate"),
            _step_json(context, "stamp_policy"),
            _step_json(context, "transition"),
            _step_json(context, "render_packet"),
            opts,
        )
        return StepOutput(raw=json.dumps(payload), json=payload)


def _step_json(context: Any, name: str) -> dict[str, Any]:
    outputs = getattr(context, "outputs", {}) or {}
    step_out = outputs.get(name)
    json_out = getattr(step_out, "json", None) if step_out is not None else None
    return json_out if isinstance(json_out, dict) else {}
