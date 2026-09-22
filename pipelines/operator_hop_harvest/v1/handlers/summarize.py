"""Optional one-paragraph summary (include_summary=true only)."""

from __future__ import annotations

import json
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import step_output_json


class OperatorHopHarvestSummarizeHandler(BaseHandler):
    step_type = "operator_hop_harvest_summarize_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        outputs = getattr(context, "step_outputs", {}) or {}
        assembled = step_output_json(outputs, "assemble")
        view = assembled.get("view") if isinstance(assembled.get("view"), dict) else assembled
        summary = json.dumps(view, ensure_ascii=False)[:500]
        out = {"summary_paragraph": summary}
        return StepOutput(raw=json.dumps(out), json=out)
