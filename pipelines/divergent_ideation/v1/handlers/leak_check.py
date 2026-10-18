"""leak_check_v1 and leak_check_final pipeline handlers."""

from __future__ import annotations

import json
from typing import Any, override

from divergent_ideation.leak_check_v1 import check_leak, finalize_leak_reports
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import PipelineContext, StepOutput
from systems.pipeline.core.schemas import StepConfig


def _json_output(payload: dict[str, Any]) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload)


class LeakCheckV1Handler(BaseHandler):
    step_type = "leak_check_v1"

    @override
    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        resolver = NamespaceResolver(context)
        inputs = step.handler_inputs or {}
        abstract = self._resolve_input(resolver, step, "abstract_problem", inputs)
        terms = self._resolve_input(resolver, step, "domain_terms_to_strip", inputs)
        report = check_leak(dict(abstract or {}), list(terms or []))
        accepted = dict(abstract or {})
        payload = {"leak_report": report, "accepted_abstract_problem": accepted}
        return _json_output(payload)


class LeakCheckFinalV1Handler(BaseHandler):
    step_type = "leak_check_final_v1"

    @override
    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        reports: list[dict[str, Any] | None] = []
        abstracts: list[dict[str, Any] | None] = []
        for entry in step.reads_from or []:
            step_name = entry.get("step")
            if not step_name:
                continue
            out = context.get_output(step_name)
            data = out.json if out and isinstance(out.json, dict) else {}
            if data.get("_skipped"):
                reports.append({"_skipped": True})
                abstracts.append(None)
                continue
            reports.append(data.get("leak_report") or data)
            abstracts.append(data.get("accepted_abstract_problem"))
        final_report, accepted = finalize_leak_reports(reports, abstracts)
        payload = {"leak_report": final_report, "accepted_abstract_problem": accepted}
        return _json_output(payload)
