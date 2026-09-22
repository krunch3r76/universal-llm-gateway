"""Terminal emit step."""

from __future__ import annotations

import json
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import step_output_json


class OperatorHopHarvestEmitHandler(BaseHandler):
    step_type = "operator_hop_harvest_emit_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        outputs = getattr(context, "step_outputs", {}) or {}
        assembled = step_output_json(outputs, "assemble")
        view = assembled.get("view") if isinstance(assembled.get("view"), dict) else assembled
        summary_step = step_output_json(outputs, "summarize")
        if summary_step.get("summary_paragraph"):
            view = dict(view)
            view["summary_paragraph"] = summary_step["summary_paragraph"]
        return StepOutput(raw=json.dumps(view), json=view)
