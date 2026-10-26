"""POST birth plan to GIW when op=start."""

from __future__ import annotations

import json
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

GIW_RUNS_URL = "http://127.0.0.1:8765/api/v1/mechanical-land/runs"


def post_mechanical_land_run(plan: dict[str, Any]) -> dict[str, Any]:
    """Impure: POST plan to GIW. Patched in unit tests."""
    import urllib.error
    import urllib.request

    body = json.dumps(plan).encode("utf-8")
    req = urllib.request.Request(
        GIW_RUNS_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def evaluate_commit(
    validate_out: dict[str, Any], plan_out: dict[str, Any]
) -> dict[str, Any]:
    if validate_out.get("op") != "start":
        return {"skipped": True, "phase": "commit", "http": False}
    if plan_out.get("admitted") is not True:
        return {"skipped": True, "phase": "commit", "http": False}
    result = post_mechanical_land_run(plan_out)
    return {"phase": "commit", "http": True, "result": result}


class MechanicalLandCommitHandler(BaseHandler):
    step_type = "mechanical_land_commit_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        validate_out = _step_json(context, "validate")
        plan_out = _step_json(context, "plan")
        payload = evaluate_commit(validate_out, plan_out)
        return StepOutput(raw=json.dumps(payload), json=payload)


def _step_json(context: Any, name: str) -> dict[str, Any]:
    outputs = getattr(context, "outputs", {}) or {}
    step_out = outputs.get(name)
    json_out = getattr(step_out, "json", None) if step_out is not None else None
    return json_out if isinstance(json_out, dict) else {}
