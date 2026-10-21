"""Apply pipeline defaults to parsed divergent-ideation problem JSON."""

from __future__ import annotations

import json
from typing import Any, override

from divergent_ideation.leak_check_v1 import apply_problem_defaults
from systems.pipeline.core.execution.resolver import NamespaceResolver
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import PipelineContext, StepOutput
from systems.pipeline.core.schemas import StepConfig


def _json_output(payload: dict[str, Any]) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload)


class ProblemDefaultsV1Handler(BaseHandler):
    step_type = "problem_defaults_v1"

    @override
    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        resolver = NamespaceResolver(context)
        inputs = step.handler_inputs or {}
        problem = self._resolve_input(resolver, step, "problem", inputs)
        default_run_seed = self._resolve_input(resolver, step, "default_run_seed", inputs)
        if not isinstance(problem, dict):
            raise ValueError(
                f"Step '{step.id}': handler_inputs.problem must resolve to a dict"
            )
        seed = int(default_run_seed or 0)
        return _json_output(apply_problem_defaults(problem, default_run_seed=seed))
