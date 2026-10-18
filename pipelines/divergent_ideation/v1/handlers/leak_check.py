"""leak_check_v1 pipeline handler (per-attempt check and terminal merge)."""

from __future__ import annotations

import json
from typing import Any, override

from divergent_ideation.events import DivergentIdeationLeakHalted
from divergent_ideation.leak_check_v1 import (
    build_rag_query,
    check_leak,
    finalize_leak_reports,
)
from divergent_ideation.map_enrich_v1 import inject_decontextualizer_model
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
        if step.reads_from:
            return await self._execute_finalize(step, context)
        resolver = NamespaceResolver(context)
        inputs = step.handler_inputs or {}
        abstract = self._resolve_input(resolver, step, "abstract_problem", inputs)
        terms = self._resolve_input(resolver, step, "domain_terms_to_strip", inputs)
        decontextualizer_model = self._resolve_input(
            resolver, step, "decontextualizer_model", inputs
        )
        accepted = inject_decontextualizer_model(
            dict(abstract or {}), str(decontextualizer_model or "") or None
        )
        report = check_leak(accepted, list(terms or []))
        payload = {"leak_report": report, "accepted_abstract_problem": accepted}
        return _json_output(payload)

    async def _execute_finalize(
        self, step: StepConfig, context: PipelineContext
    ) -> StepOutput:
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
        accepted = accepted or {}
        rag_query = build_rag_query(accepted) if accepted else ""
        passed = bool(final_report.get("passed"))
        payload = {
            "passed": passed,
            "leak_report": final_report,
            "accepted_abstract_problem": accepted,
            "rag_query": rag_query,
        }
        if not passed and final_report.get("hits"):
            self._publish_bus_event(
                context,
                DivergentIdeationLeakHalted(leak_report=final_report),
            )
        return _json_output(payload)
