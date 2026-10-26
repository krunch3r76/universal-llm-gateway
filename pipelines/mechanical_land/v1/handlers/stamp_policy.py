"""Stamp policy on op=start."""

from __future__ import annotations

import json
from typing import Any, override

from mechanical_land.policy import PolicyInvalidError, stamp_policy
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput


def evaluate_stamp_policy(
    validate_out: dict[str, Any], opts: dict[str, Any]
) -> dict[str, Any]:
    if validate_out.get("admitted") is False:
        return dict(validate_out)
    if validate_out.get("op") != "start":
        return {"skipped": True, "phase": "stamp_policy"}
    explicit = opts.get("policy") if isinstance(opts.get("policy"), dict) else None
    todo = opts.get("todo") if isinstance(opts.get("todo"), dict) else None
    try:
        policy, sha = stamp_policy(explicit=explicit, todo_attrs=todo)
    except PolicyInvalidError as exc:
        return {
            "admitted": False,
            "phase": "stamp_policy",
            "status_code": 422,
            "field": "policy",
            "code": exc.code,
            "reason": exc.detail,
        }
    return {"policy": policy, "policy_sha256": sha, "phase": "stamp_policy"}


class MechanicalLandStampPolicyHandler(BaseHandler):
    step_type = "mechanical_land_stamp_policy_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = dict(getattr(context, "options", {}) or {})
        validate_out = _step_json(context, "validate")
        payload = evaluate_stamp_policy(validate_out, opts)
        return StepOutput(raw=json.dumps(payload), json=payload)


def _step_json(context: Any, name: str) -> dict[str, Any]:
    outputs = getattr(context, "outputs", {}) or {}
    step_out = outputs.get(name)
    json_out = getattr(step_out, "json", None) if step_out is not None else None
    return json_out if isinstance(json_out, dict) else {}
