"""
AssessLoopHandler class for the assess_loop_v1 step type.

Thin class-delegator (mirrors the ``generate/`` package pattern): the handler
owns ``step_type``, the ``@register_handler`` registry side effect, the
``execute`` entry point (which delegates to
:func:`.loop_runner.run_assess_loop`), and ``validate``. All loop orchestration
and per-phase logic live in the sibling submodules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from universal_logging import get_logger

from ..builtin import BaseHandler
from ..registry import register_handler
from .loop_runner import run_assess_loop

if TYPE_CHECKING:
    from ...schemas import StepConfig
    from ..protocol import PipelineContext, StepOutput

logger = get_logger(__name__)


@register_handler
class AssessLoopHandler(BaseHandler):
    """
    Engine-mediated iterative assess→act loop.

    Each iteration the assessor model returns a structured JSON decision;
    the engine dispatches the named action (a separate model call) or
    exits when the terminal action is reached or the budget is exhausted.

    Key property: works with any instruction-following model that produces
    reliable structured output. No tool-calling training required — the
    model makes decisions via JSON, the engine dispatches.

    See assess_loop_config.py for YAML field documentation and loop_runner.py
    for the lifecycle invariant (AssessLoopStarted ⟹ ∃! AssessLoopCompleted).
    """

    step_type: str = "assess_loop_v1"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        return await run_assess_loop(self, step, context)

    @override
    def validate(self, step: StepConfig) -> list[str]:
        from ..assess_loop_config import AssessLoopConfig

        errors = []
        if not step.model_ref:
            errors.append(f"Step '{step.id}' missing model_ref")
        if not step.prompt_ref and not step.get_domain_field("assess_handler"):
            errors.append(f"Step '{step.id}' missing prompt_ref")
        if not step.handler_inputs:
            errors.append(f"Step '{step.id}' missing handler_inputs")
        errors.extend(AssessLoopConfig.from_step(step).validate(step.id))
        return errors
