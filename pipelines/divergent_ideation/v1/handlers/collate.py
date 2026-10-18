"""collate_v1 handler."""

from __future__ import annotations

import json
from typing import Any, override

from divergent_ideation.collate_v1 import build_run_manifest, map_success_fraction
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import PipelineContext, StepOutput
from systems.pipeline.core.schemas import StepConfig


class CollateV1Handler(BaseHandler):
    step_type = "collate_v1"

    @override
    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        resolver = NamespaceResolver(context)
        inputs = step.handler_inputs or {}
        payload: dict[str, Any] = {}
        for key in (
            "problem",
            "pool_manifest",
            "leak_report",
            "operators",
            "operator_subsets",
            "operator_dispersion",
            "abstract_briefs",
            "briefs",
            "audit_assignments",
            "audits",
            "run_id",
            "parse_failures",
            "assignment_error",
        ):
            if key in inputs:
                payload[key] = self._resolve_input(resolver, step, key, inputs)

        skipped_steps: dict[str, bool] = {}
        for name in (
            "retrieve_operators",
            "operator_split",
            "map_ideate",
            "re_instantiate",
            "audit_assign",
            "map_audit",
        ):
            out = context.get_output(name)
            if out and isinstance(out.json, dict) and out.json.get("_skipped"):
                skipped_steps[name] = True
        payload["skipped_steps"] = skipped_steps

        map_failure = False
        for map_step in ("map_ideate", "re_instantiate", "map_audit"):
            out = context.get_output(map_step)
            hints = getattr(out, "failed_iteration_hints", None) if out else None
            total = getattr(out, "total_iterations", None) if out else None
            if hints is not None and total is not None:
                if map_success_fraction(len(hints), int(total)):
                    map_failure = True
        payload["map_failure"] = map_failure

        problem = payload.get("problem") or {}
        payload["problem_id"] = problem.get("problem_id")
        payload["run_seed"] = problem.get("run_seed")
        payload["benchmark_tag"] = problem.get("benchmark_tag")
        assign_out = context.get_output("audit_assign")
        if assign_out and isinstance(assign_out.json, dict):
            payload.setdefault(
                "assignment_error", assign_out.json.get("assignment_error")
            )
        manifest = build_run_manifest(payload)
        return StepOutput(raw=json.dumps(manifest), json=manifest)
