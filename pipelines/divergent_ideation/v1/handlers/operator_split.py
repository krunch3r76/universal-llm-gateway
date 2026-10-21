"""operator_split_v1 handler."""

from __future__ import annotations

import json
from typing import override

from divergent_ideation.events import DivergentIdeationOperatorSplit
from divergent_ideation.operator_split_v1 import split_operators
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import PipelineContext, StepOutput
from systems.pipeline.core.schemas import StepConfig


class OperatorSplitV1Handler(BaseHandler):
    step_type = "operator_split_v1"

    @override
    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        resolver = NamespaceResolver(context)
        inputs = step.handler_inputs or {}
        operators = self._resolve_input(resolver, step, "operators", inputs)
        pool_manifest = self._resolve_input(resolver, step, "pool_manifest", inputs)
        run_seed = int(self._resolve_input(resolver, step, "run_seed", inputs) or 0)
        result = split_operators(operators, list(pool_manifest or []), run_seed)
        self._publish_bus_event(
            context,
            DivergentIdeationOperatorSplit(
                operator_dispersion=float(result.get("operator_dispersion") or 0.0),
                pool_size=len(list(pool_manifest or [])),
            ),
        )
        return StepOutput(raw=json.dumps(result), json=result)
