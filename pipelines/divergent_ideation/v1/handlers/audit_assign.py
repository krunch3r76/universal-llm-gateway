"""audit_assign_v1 handler."""

from __future__ import annotations

import json
from typing import override

from divergent_ideation.audit_assign_v1 import assign_auditors
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
        briefs = self._resolve_input(resolver, step, "briefs", inputs)
        pool_manifest = self._resolve_input(resolver, step, "pool_manifest", inputs)
        run_seed = int(self._resolve_input(resolver, step, "run_seed", inputs) or 0)
        min_auditors = int(self._resolve_input(resolver, step, "min_auditors", inputs) or 2)
        result = assign_auditors(
            list(briefs or []),
            list(pool_manifest or []),
            run_seed,
            min_auditors=min_auditors,
        )
        return StepOutput(raw=json.dumps(result), json=result)
