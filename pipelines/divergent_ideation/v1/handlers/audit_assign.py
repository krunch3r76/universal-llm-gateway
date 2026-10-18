"""audit_assign_v1 handler."""

from __future__ import annotations

import json
from typing import Any, override

from divergent_ideation.audit_assign_v1 import assign_auditors
from divergent_ideation.events import DivergentIdeationAuditAssignments
from divergent_ideation.map_enrich_v1 import (
    abstract_briefs_from_map_ideate,
    briefs_from_re_instantiate,
    inject_briefs_for_assignment,
)
from systems.pipeline.core.execution.map_reduce.collection import MapOutputCollection
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import PipelineContext, StepOutput
from systems.pipeline.core.schemas import StepConfig


class AuditAssignV1Handler(BaseHandler):
    step_type = "audit_assign_v1"

    @override
    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        resolver = NamespaceResolver(context)
        inputs = step.handler_inputs or {}
        pool_manifest = list(self._resolve_input(resolver, step, "pool_manifest", inputs) or [])
        run_seed = int(self._resolve_input(resolver, step, "run_seed", inputs) or 0)
        min_auditors = int(self._resolve_input(resolver, step, "min_auditors", inputs) or 2)
        run_id = str(context.execution_id or "")

        map_ideate = context.get_output("map_ideate")
        re_inst = context.get_output("re_instantiate")
        split_out = context.get_output("operator_split")
        split_json = split_out.json if split_out and isinstance(split_out.json, dict) else {}
        pool_models = list(split_json.get("pool_models") or [])
        briefs_per_model = int(context.pipeline.options.get("briefs_per_model", 3))

        abstract_briefs: list[dict[str, Any]] = []
        if isinstance(map_ideate, MapOutputCollection):
            abstract_briefs = abstract_briefs_from_map_ideate(
                map_ideate.outputs_aligned(),
                pool_models,
                pool_manifest,
                run_id,
                briefs_per_model,
            )

        briefs: list[dict[str, Any]] = []
        if isinstance(re_inst, MapOutputCollection):
            briefs = briefs_from_re_instantiate(
                re_inst.outputs_aligned(),
                abstract_briefs,
                pool_manifest,
            )
        else:
            raw_briefs = self._resolve_input(resolver, step, "briefs", inputs)
            briefs = inject_briefs_for_assignment(list(raw_briefs or []))

        result = assign_auditors(briefs, pool_manifest, run_seed, min_auditors=min_auditors)
        assignments = list(result.get("audit_assignments") or [])
        self._publish_bus_event(
            context,
            DivergentIdeationAuditAssignments(
                pool_size=len(pool_manifest),
                assignment_count=len(assignments),
            ),
        )
        return StepOutput(raw=json.dumps(result), json=result)
