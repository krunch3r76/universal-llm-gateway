"""Fold journal and compute next transition."""

from __future__ import annotations

import json
from typing import Any, override

from mechanical_land.fold import fold
from mechanical_land.schema import JournalEntry
from mechanical_land.transition import transition as compute_transition
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput


def evaluate_transition(
    validate_out: dict[str, Any],
    stamp_out: dict[str, Any],
    opts: dict[str, Any],
) -> dict[str, Any]:
    if validate_out.get("admitted") is False:
        return dict(validate_out)
    journal_raw = opts.get("journal") or []
    events: list[JournalEntry] = []
    if isinstance(journal_raw, list):
        for row in journal_raw:
            if isinstance(row, dict):
                events.append(JournalEntry(**row))
    state = fold(events)
    policy = stamp_out.get("policy") or opts.get("policy") or {}
    if isinstance(stamp_out.get("policy"), dict):
        policy = stamp_out["policy"]
    tr = compute_transition(policy, state)
    out = tr.to_dict()
    out["phase"] = "transition"
    return out


class MechanicalLandTransitionHandler(BaseHandler):
    step_type = "mechanical_land_transition_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = dict(getattr(context, "options", {}) or {})
        validate_out = _step_json(context, "validate")
        stamp_out = _step_json(context, "stamp_policy")
        payload = evaluate_transition(validate_out, stamp_out, opts)
        return StepOutput(raw=json.dumps(payload), json=payload)


def _step_json(context: Any, name: str) -> dict[str, Any]:
    outputs = getattr(context, "outputs", {}) or {}
    step_out = outputs.get(name)
    json_out = getattr(step_out, "json", None) if step_out is not None else None
    return json_out if isinstance(json_out, dict) else {}
